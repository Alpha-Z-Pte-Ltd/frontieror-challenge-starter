"""The format checks on a solver's output, the part of the evaluation that can run without the private checker.

The sandbox scores the numeric `objective_value` of /work/out/solution.json once the checker has confirmed it matches
the decisions. Locally that last part cannot be checked: a solution that passes here can still be infeasible.
"""

import json
import math
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from frontieror.constants import MAX_ERROR_CHARS, MAX_ERRORS, MAX_SOLUTION_BYTES


@dataclass(frozen=True)
class OutputCheck:
    """Represents the result of checking a solver's output."""

    status: str  # ok, no_output, too_large, invalid_json or invalid_format.
    message: str
    objective: float | None = None


def check_output(path: Path, json_schema: str) -> OutputCheck:
    """Check the solver's output file at `path` against the problem's JSON Schema."""
    if not path.is_file():
        return OutputCheck("no_output", "The solver wrote no solution file at --output.")

    size = path.stat().st_size
    if size > MAX_SOLUTION_BYTES:
        limit = MAX_SOLUTION_BYTES // 1024 // 1024
        return OutputCheck("too_large", f"The solution file is too large: {size / 1024 / 1024:.1f} MB > {limit} MB.")

    try:
        solution = json.loads(path.read_bytes())
    except (ValueError, UnicodeDecodeError) as exc:
        return OutputCheck("invalid_json", f"The solution file is not valid JSON: {exc}")
    return check_solution(solution, json_schema)


def check_solution(solution: Any, json_schema: str) -> OutputCheck:
    """Check the solution against the problem's JSON Schema."""
    if not isinstance(solution, dict):
        return OutputCheck("invalid_format", "The solution must be a JSON object.")

    objective = solution.get("objective_value")
    if not is_objective(objective):
        return OutputCheck("invalid_format", "objective_value: must be a finite number, since it is what gets scored")

    validator = Draft202012Validator(json.loads(json_schema))
    errors = [
        f"{_where(e)}: {e.message[:MAX_ERROR_CHARS]}" for e in islice(validator.iter_errors(solution), MAX_ERRORS)
    ]
    if errors:
        return OutputCheck("invalid_format", "\n".join(errors))

    return OutputCheck("ok", "Valid format", objective=float(objective))


def is_objective(value: Any) -> bool:
    """Whether `value` can be an objective_value: a finite number, not a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # An int too large for a float.
        return False


def _where(error: ValidationError) -> str:
    """The error's place in the solution, such as routes[0].nodes."""
    where = ""
    for part in error.absolute_path:
        if isinstance(part, int):
            where += f"[{part}]"
        elif where:
            where += f".{part}"
        else:
            where = str(part)
    return where or "the solution"
