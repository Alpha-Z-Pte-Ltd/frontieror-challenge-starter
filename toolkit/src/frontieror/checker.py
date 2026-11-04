"""Runs a problem's published checker on a solution, as the sandbox does after the solver:

    python feasibility_check.py --instance_path <instance> --solution_path <solution> --result_path <result>

natively with the solver environment's Python, or with Docker in the image the sandbox runs checkers in, under the
sandbox's limits. The result has `feasible`, `violations` and `computed_objective_value`, the objective that is scored.
"""

import json
import math
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from frontieror.constants import CHECKER_TIMEOUT_S, DOCKER_START_GRACE_S, MAX_ERROR_CHARS, MAX_ERRORS
from frontieror.environment import docker_run
from frontieror.run import SANDBOX_LIMITS


@dataclass(frozen=True)
class Verdict:
    """The checker's verdict on a solution."""

    status: str  # ok, infeasible or checker_error.
    message: str
    objective: float | None = None


def run_checker(
    root: Path, checker: Path, instance: Path, solution: Path, result: Path, python: Path | None, image_ref: str | None
) -> Verdict:
    """Runs the checker in a new folder `root`, removed afterwards, and copies its result to `result`. The checker
    runs from its own folder, read-only with Docker, as some read other files there: modules they import, or a
    witness checker's model of the instance.

    Args:
        root: A folder that does not exist yet.
        checker: The problem's feasibility_check.py, in its folder.
        instance: The instance the solution is for.
        solution: The solution to check.
        result: Where to keep the checker's result.
        python: The Python to run the checker with natively.
        image_ref: The image to run the checker in with Docker, used instead of `python` when given.
    """
    for folder in ("in", "out"):
        (root / folder).mkdir(parents=True)
    shutil.copyfile(instance, root / "in" / "instance.json")
    shutil.copyfile(solution, root / "in" / "solution.json")

    try:
        if image_ref:
            code, log = _run_docker(root, checker, image_ref)
        else:
            code, log = _run_native(root, checker, python)

        output = root / "out" / "result.json"
        if output.is_file():
            shutil.copyfile(output, result)
        return _verdict(code, output, log)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run_native(root: Path, checker: Path, python: Path | None) -> tuple[int | None, str]:
    command = [
        str(python),
        str(checker.resolve()),
        "--instance_path", "in/instance.json",
        "--solution_path", "in/solution.json",
        "--result_path", "out/result.json",
    ]  # fmt: skip
    try:
        done = subprocess.run(
            command,
            cwd=root,
            env={"HOME": str(root), "LANG": os.environ.get("LANG", "C.UTF-8"), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            errors="replace",
            timeout=CHECKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, ""
    return done.returncode, done.stdout + done.stderr


def _run_docker(root: Path, checker: Path, image_ref: str) -> tuple[int | None, str]:
    name = f"frontieror-check-{uuid.uuid4().hex[:12]}"
    command = docker_run(
        image_ref,
        [
            "python", f"/work/checker/{checker.name}",
            "--instance_path", "/work/in/instance.json",
            "--solution_path", "/work/in/solution.json",
            "--result_path", "/work/out/result.json",
        ],
        env={"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
        mounts=[
            f"{checker.parent.resolve()}:/work/checker:ro", f"{root / 'in'}:/work/in:ro", f"{root / 'out'}:/work/out"
        ],
        options=["--name", name, "--pull", "never", *SANDBOX_LIMITS],
    )  # fmt: skip
    try:
        timeout = CHECKER_TIMEOUT_S + DOCKER_START_GRACE_S
        done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        return None, ""
    return done.returncode, done.stdout + done.stderr


def _verdict(code: int | None, output: Path, log: str) -> Verdict:
    """The sandbox's reading of the checker's run: an exit code other than 0 rejects the solution."""
    if code is None:
        return Verdict("checker_error", f"The checker did not finish within {CHECKER_TIMEOUT_S} s.")
    if code != 0:
        last = next((line for line in reversed(log.strip().splitlines()) if line.strip()), "")
        return Verdict(
            "infeasible", f"Checker exited with code {code}" + (f": {last[:MAX_ERROR_CHARS]}" if last else "")
        )

    try:
        result = json.loads(output.read_text())
    except OSError, ValueError:
        return Verdict("checker_error", "The checker wrote no readable result.")

    if not result.get("feasible"):
        violations = result.get("violations") or result.get("violated_constraints") or ["no details"]
        lines = [str(v)[:MAX_ERROR_CHARS] for v in violations[:MAX_ERRORS]]
        return Verdict("infeasible", "\n".join(["Infeasible:", *lines]))

    objective = result.get("computed_objective_value")
    if isinstance(objective, bool) or not isinstance(objective, (int, float)) or not math.isfinite(objective):
        return Verdict("checker_error", "The checker computed no objective.")
    return Verdict("ok", "Feasible", float(objective))
