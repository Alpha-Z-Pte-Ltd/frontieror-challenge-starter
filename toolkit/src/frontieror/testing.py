"""Testing a solver on an instance: the run (run.py) and the format checks (checks.py), as one result."""

import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from frontieror.checks import check_output
from frontieror.config import ConfigError, Project
from frontieror.data import Image, Instance, Problem
from frontieror.environment import SolverEnv, ensure_docker, ensure_local
from frontieror.files import empty_folder
from frontieror.run import Workspace, run_docker, run_native


@dataclass(frozen=True)
class InstanceResult:
    """The result of running a solver on one instance and checking its output's format.

    Attributes:
        instance: The instance's name.
        status: ok, crash, timeout, no_output, too_large, invalid_json or invalid_format.
        message: What happened, such as the format errors of the solution.
        wall_s: How long the run took, in seconds.
        time_limit_s: The instance's time limit, in seconds.
        objective: The objective_value the solution reports, None unless the format is valid.
        log: The end of the solver's output, stdout and stderr together.
        output_dir: The folder that keeps the run's log.txt and solution.json.
    """

    instance: str
    status: str
    message: str
    wall_s: float
    time_limit_s: int
    objective: float | None
    log: str = ""
    output_dir: Path | None = None

    @property
    def ok(self) -> bool:
        """Whether the solver finished in time and its output has the right format."""
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
                if subprocess.run(["docker", "image", "inspect", self.image.pull], capture_output=True).returncode != 0:
                    self.log(f"Pulling {self.image.pull}...")
                    try:
                        subprocess.run(["docker", "pull", "--platform", "linux/amd64", self.image.pull], check=True)
                    except subprocess.CalledProcessError as exc:
                        raise ConfigError(f"Could not pull {self.image.pull}; see Docker's output above.") from exc
                self._docker_env = ensure_docker(self.project, self.image, self.image.pull, self.log)
            else:
                self._env = ensure_local(self.project, self.image, self.log)
            self._ready = True

    def run(self, solver_dir: Path, problem: Problem, instance: Instance, keep: Path) -> InstanceResult:
        """Runs a solver on an instance the way the sandbox does, and checks its output's format.

        Args:
            solver_dir: The solver's folder, with its solve.py.
            problem: The problem, for its slug and solution schema.
            instance: The instance to run on.
            keep: The folder where the run's log.txt and solution.json are kept.

        Returns:
            The result of the run.
        """
        self.setup()

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

        base = dict(
            instance=instance.name,
            wall_s=outcome.wall_s,
            time_limit_s=instance.time_limit_s,
            log=outcome.log,
            output_dir=keep,
        )
        if outcome.status != "ok":
            return InstanceResult(status=outcome.status, message=outcome.message, objective=None, **base)

        check = check_output(keep / "solution.json", problem.solution_json_schema)
        return InstanceResult(status=check.status, message=check.message, objective=check.objective, **base)


def _require_docker() -> None:
    try:
        subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError("Docker is not available. Start Docker, or test without --docker.") from exc
