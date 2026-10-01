import json
import sys
from pathlib import Path

import pytest

from frontieror.config import Project
from frontieror.data import Image, Instance, Problem
from frontieror.environment import SolverEnv
from frontieror.testing import Runner

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "objective_value": {"type": "number"},
        "routes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "vehicle": {"type": "integer"},
                    "nodes": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["vehicle", "nodes"],
            },
        },
    },
    "required": ["objective_value", "routes"],
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(root=tmp_path, requirements=[], agent={})


@pytest.fixture
def problem(tmp_path: Path) -> Problem:
    instance = tmp_path / "tiny.json"
    instance.write_text(json.dumps({"n": 3}))
    return Problem(
        slug="toy",
        name="Toy routing",
        objective="minimize",
        description="Route one vehicle.",
        instance_schema='{"n": "<int> Nodes."}',
        solution_schema="{}",
        solution_json_schema=json.dumps(JSON_SCHEMA),
        instances=[Instance("tiny", instance, time_limit_s=2)],
    )


@pytest.fixture
def runner(project: Project) -> Runner:
    """Runs solvers with the interpreter running the tests, instead of building the image's environment."""
    image = Image(
        id="python-3.14",
        python_version="3.14",
        pull=None,
        packages=[],
        requirements_lock="",
        max_packages=50,
        max_requirements_bytes=10240,
    )
    env = SolverEnv(
        venv=Path(sys.prefix), python=Path(sys.executable), requirements_lock=project.root / "requirements.lock"
    )
    return Runner(project, image, docker=False, log=lambda _: None, env=env)
