"""Testing a solver on an instance: the run (run.py), the format checks (checks.py) and, when the problem's checker is
published, the checker (checker.py), as one result."""

import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from frontieror.checker import run_checker
from frontieror.checks import check_output
from frontieror.config import ConfigError, Project
from frontieror.data import Image, Instance, Problem
from frontieror.environment import SolverEnv, ensure_docker, ensure_local
from frontieror.files import empty_folder
from frontieror.run import Workspace, run_docker, run_native


@dataclass(frozen=True)
class InstanceResult:
    """The result of running a solver on one instance and checking its output, with the checker when it is published.

    Attributes:
        instance: The instance's name.
        status: ok, crash, timeout, no_output, too_large, invalid_json, invalid_format, infeasible or checker_error.
        message: What happened, such as the format errors or the violations of the solution.
        wall_s: How long the run took, in seconds.
        time_limit_s: The instance's time limit, in seconds.
        objective: The objective the checker recomputed, or without a checker the objective_value the solution
            reports; None unless the solution passed.
        log: The end of the solver's output, stdout and stderr together.
        output_dir: The folder that keeps the run's log.txt, solution.json and the checker's result.json.
        checked: Whether the problem's checker ran on the solution.
    """

    instance: str
    status: str
    message: str
    wall_s: float
    time_limit_s: int
    objective: float | None
    log: str = ""
    output_dir: Path | None = None
    checked: bool = False

    @property
    def ok(self) -> bool:
        """Whether the solver finished in time, its output has the right format and the checker, if any, accepts it."""
        return self.status == "ok"

    @property
    def solution(self) -> Path | None:
        """The solution file the solver wrote, or None if it wrote none."""
        path = self.output_dir / "solution.json" if self.output_dir else None
        return path if path and path.is_file() else None


@dataclass(frozen=True)
class Report:
    """The results of testing a solver on several instances.

    Attributes:
        results: One result per instance.
    """

    results: list[InstanceResult]

    @property
    def passed(self) -> bool:
        """Whether every instance passed."""
        return all(r.ok for r in self.results)

    @property
    def failures(self) -> list[InstanceResult]:
        """The results of the instances that did not pass."""
        return [r for r in self.results if not r.ok]


class Runner:
    """Runs solvers one at a time, natively or in Docker, so that one run's timing is not skewed by another's.

    Args:
        project: The project, whose runs/ folder holds the runs' working files.
        image: The image whose environment the solvers run in.
        docker: Run in the image with the sandbox's limits instead of natively.
        log: Prints a progress line.
        env: An existing environment to run natively in, instead of building one from the image.
    """

    def __init__(
        self,
        project: Project,
        image: Image,
        docker: bool = False,
        log: Callable[[str], None] = print,
        env: SolverEnv | None = None,
    ):
        self.project = project
        self.image = image
        self.docker = docker
        self.log = log

        self._lock = threading.Lock()
        self._setup_lock = threading.Lock()
        self._env = env
        self._docker_env: Path | None = None
        self._ready = env is not None and not docker
        self._checker_ready = not docker

    def setup(self) -> None:
        """Builds the solver environment, and for Docker pulls the image; `run` calls it before the first run.

        Raises:
            ConfigError: Docker is needed but not available, or the image could not be pulled.
            EnvironmentBuildError: The environment could not be built.
        """
        with self._setup_lock:
            if self._ready:
                return

            if self.docker:
                if not self.image.pull:
                    raise ConfigError("The site doesn't provide a pullable image yet, so --docker cannot run.")

                _require_docker()
                self._pull(self.image.pull)
                self._docker_env = ensure_docker(self.project, self.image, self.image.pull, self.log)
            else:
                self._env = ensure_local(self.project, self.image, self.log)
            self._ready = True

    def setup_checker(self) -> None:
        """For Docker, pulls the image the sandbox runs checkers in; `run` calls it before the first check.

        Raises:
            ConfigError: The image is not published or could not be pulled.
        """
        with self._setup_lock:
            if self._checker_ready:
                return
            if not self.image.checker_pull:
                raise ConfigError("The site doesn't provide a pullable checker image, so --docker cannot run checkers.")
            self._pull(self.image.checker_pull)
            self._checker_ready = True

    def _pull(self, image_ref: str) -> None:
        """Pulls the image's linux/amd64 build unless Docker has it already."""
        if subprocess.run(["docker", "image", "inspect", image_ref], capture_output=True).returncode == 0:
            return
        self.log(f"Pulling {image_ref}...")
        try:
            subprocess.run(["docker", "pull", "--platform", "linux/amd64", image_ref], check=True)
        except subprocess.CalledProcessError as exc:
            raise ConfigError(f"Could not pull {image_ref}; see Docker's output above.") from exc

    def run(self, solver_dir: Path, problem: Problem, instance: Instance, keep: Path) -> InstanceResult:
        """Runs a solver on an instance the way the sandbox does, checks its output's format, then runs the problem's
        checker on it when the checker is published.

        Args:
            solver_dir: The solver's folder, with its solve.py.
            problem: The problem, for its slug, solution schema and checker.
            instance: The instance to run on.
            keep: The folder where the run's log.txt, solution.json and the checker's result.json are kept.

        Returns:
            The result of the run.
        """
        self.setup()
        if problem.checker:
            self.setup_checker()

        with self._lock:
            root = self.project.runs_dir / ".work" / uuid.uuid4().hex

            with Workspace.create(root, solver_dir, instance.path) as work:
                if self.docker:
                    outcome = run_docker(work, problem.slug, instance.time_limit_s, self.image.pull, self._docker_env)
                else:
                    outcome = run_native(work, problem.slug, instance.time_limit_s, self._env)

                empty_folder(keep)
                shutil.copyfile(work.log, keep / "log.txt")

                if work.output.is_file():
                    shutil.copyfile(work.output, keep / "solution.json")

            check, checked = None, False
            if outcome.status == "ok":
                check = check_output(keep / "solution.json", problem.solution_json_schema)
                if check.status == "ok" and problem.checker:
                    checked = True
                    check = run_checker(
                        self.project.runs_dir / ".work" / uuid.uuid4().hex,
                        problem.checker,
                        instance.path,
                        keep / "solution.json",
                        keep / "result.json",
                        python=None if self.docker else self._env.python,
                        image_ref=self.image.checker_pull if self.docker else None,
                    )

        base = dict(
            instance=instance.name,
            wall_s=outcome.wall_s,
            time_limit_s=instance.time_limit_s,
            log=outcome.log,
            output_dir=keep,
        )
        if check is None:
            return InstanceResult(status=outcome.status, message=outcome.message, objective=None, **base)

        return InstanceResult(
            status=check.status, message=check.message, objective=check.objective, checked=checked, **base
        )


def _require_docker() -> None:
    try:
        subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError("Docker is not available. Start Docker, or test without --docker.") from exc
