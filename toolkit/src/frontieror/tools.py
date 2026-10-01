"""What your agent gets from the toolkit. `frontieror find-solvers` calls the agent's entry (frontieror.toml) as

    find_solver(problem: Problem, tools: Tools, settings: dict) -> None

once per problem; the default one is exported from agent/__init__.py and lives in agent/main.py. The toolkit only
runs and checks code: it never writes prompts.
"""

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from frontieror.config import Project
from frontieror.constants import ENTRY, OTHER_ENTRY
from frontieror.data import Problem
from frontieror.environment import packages
from frontieror.testing import Report, Runner

# Appended to code that defines `solve(instance, time_limit_s) -> dict`: runs it with the sandbox's arguments and
# writes the solution. numpy values in the solution are converted to plain ones; NaN and infinity are refused, since
# they are not valid JSON.
DATA_LOADER = """

if __name__ == "__main__":
    import argparse, json

    def _plain(value):
        if hasattr(value, "tolist"):  # numpy arrays and scalars
            return value.tolist()
        raise TypeError(f"{type(value).__name__} is not JSON serializable: return dicts, lists, str, int and float")

    parser = argparse.ArgumentParser()
    for name in ("--problem", "--instance", "--output", "--time-limit"):
        parser.add_argument(name, required=True)
    args = parser.parse_args()

    with open(args.instance) as file:
        instance = json.load(file)

    solution = solve(instance, int(args.time_limit))

    with open(args.output, "w") as file:
        json.dump(solution, file, default=_plain, allow_nan=False)
"""


@dataclass(frozen=True)
class Environment:
    """What a solver runs with, for the agent's prompt.

    Attributes:
        python_version: The Python version, such as "3.14".
        packages: The packages a solver can import: the image's and the extra ones from frontieror.toml.
    """

    python_version: str
    packages: list[str]


class Tools:
    """What the agent gets for one problem: running candidate code, saving the solver, and logging.

    Attributes:
        project: The project.
        problem: The problem the agent works on.
        environment: What the solvers run with.
    """

    def __init__(
        self,
        project: Project,
        problem: Problem,
        runner: Runner,
        log: Callable[[str], None] = print,
    ):
        self.project = project
        self.problem = problem
        self.environment = Environment(runner.image.python_version, packages(runner.image, project.requirements))
        self._runner = runner
        self._log = log
        self._candidates = 0
        self._lock = threading.Lock()

    @property
    def solver_folder(self) -> Path:
        """Path `solvers/<slug>/`, where this problem's solver goes, and what `submit` sends."""
        return self.project.solvers_dir / self.problem.slug

    @property
    def runs_dir(self) -> Path:
        """Path `runs/<slug>/`, where this problem's logs, candidates and their outputs go."""
        return self.project.runs_dir / self.problem.slug

    def test(self, code: str, instances: Iterable[str] | None = None, add_data_loader: bool = True) -> Report:
        """Runs `code` as solve.py on the public instances, all or the ones named, and checks its output.

        Args:
            code: The solver's code, which defines `solve(instance, time_limit_s) -> dict`.
            instances: The names of the instances to run; all if None.
            add_data_loader: If True, the data loading logic is appended to `code`.

        Returns:
            A report of the runs, with each instance's result and the solver's output.

        Raises:
            ValueError: An instance name is not one of the problem's public instances.
        """
        selected = self.problem.instances
        if instances is not None:
            names = set(instances)
            unknown = names - {i.name for i in selected}
            if unknown:
                raise ValueError(f"Unknown instance(s) of {self.problem.slug}: {', '.join(sorted(unknown))}")
            selected = [i for i in selected if i.name in names]

        with self._lock:
            self._candidates += 1
            folder = self.runs_dir / f"candidate-{self._candidates}"

        solver = folder / "solver"
        solver.mkdir(parents=True, exist_ok=True)
        (solver / ENTRY).write_text(_source(code, add_data_loader))

        return Report(
            [self._runner.run(solver, self.problem, instance, folder / instance.name) for instance in selected]
        )

    def save(self, code: str, add_data_loader: bool = True) -> Path:
        """Writes the solver to solvers/<slug>/solve.py: what `test` checks and `submit` sends.

        Args:
            code: The solver's code, which defines `solve(instance, time_limit_s) -> dict`.
            add_data_loader: If True, the data loading logic is appended to `code`.

        Returns:
            The path to the solver file.

        Raises:
            FileExistsError: The folder already holds an executable `solve`.
        """
        folder = self.solver_folder
        if (folder / OTHER_ENTRY).exists():
            raise FileExistsError(
                f"{folder / OTHER_ENTRY} exists; a problem folder holds solve.py or solve, never both."
            )
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ENTRY
        path.write_text(_source(code, add_data_loader))
        return path

    def log_file(self, name: str, text: str) -> Path:
        """Writes `text` to runs/<slug>/<name>, for logging long texts such as the model's replies.

        Args:
            name: The file's name within runs/<slug>/.
            text: The text to write to the log file.

        Returns:
            The path to the log file.
        """
        path = self.runs_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def log(self, message: str) -> None:
        """Prints a progress line, prefixed with the problem's slug.

        Args:
            message: The message to print.
        """
        self._log(f"[{self.problem.slug}] {message}")


def _source(code: str, add_data_loader: bool) -> str:
    """Code as a file holds it, with the data loader appended when asked, ending with one newline."""
    return (code.rstrip() + (DATA_LOADER if add_data_loader else "")).rstrip() + "\n"
