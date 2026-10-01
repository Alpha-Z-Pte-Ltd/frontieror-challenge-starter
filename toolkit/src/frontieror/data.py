"""The current stage's problems and the docker image, downloaded by `frontieror init` into data/ and read from there.

data/problems/<slug>/problem.json          statement and schemas, as the site publishes them
data/problems/<slug>/instances/<name>.json the public instances
data/problems/<slug>/instances/index.json  their ids and time limits
data/image/image.json                      the python-3.14 docker image: Python version, packages and limits
data/image/requirements.lock               the image's packages and their locked versions
"""

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from frontieror.api import api
from frontieror.config import ConfigError, Project
from frontieror.constants import IMAGE
from frontieror.files import empty_folder

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Instance:
    """A public instance of a problem.

    Attributes:
        name: The instance's name, unique within its problem.
        path: The instance file, JSON in the problem's instance schema.
        time_limit_s: The seconds a solver gets for it.
    """

    name: str
    path: Path
    time_limit_s: int


@dataclass(frozen=True)
class Problem:
    """A problem of the current stage, with its public instances.

    Attributes:
        slug: The problem's id, which also names its folder in solvers/.
        name: The problem's title.
        objective: "minimize" or "maximize".
        description: The problem statement in Markdown, with TeX math.
        instance_schema: An annotated example of an instance, as JSON text.
        solution_schema: An annotated example of a solution, as JSON text.
        solution_json_schema: The solution's JSON Schema, as JSON text; it checks the format only.
        instances: The public instances.
    """

    slug: str
    name: str
    objective: str
    description: str
    instance_schema: str
    solution_schema: str
    solution_json_schema: str
    instances: list[Instance]


@dataclass(frozen=True)
class Image:
    """The docker image the solvers run in.

    Attributes:
        id: The image's id on the site, such as "python-3.14".
        python_version: The image's Python version.
        pull: Where to `docker pull` the image from, or None if it is not published yet.
        packages: The image's preinstalled packages.
        requirements_lock: The image's installed packages with their exact versions, in requirements format.
        max_packages: How many extra packages a submission may list.
        max_requirements_bytes: How long the list of extra packages may be.
    """

    id: str
    python_version: str
    pull: str | None
    packages: list[str]
    requirements_lock: str
    max_packages: int
    max_requirements_bytes: int


def download_data(project: Project, log: Callable[[str], None] = print) -> list[Problem]:
    """Replaces data/ with the current stage's problems, their public instances, and the image's lock.

    Args:
        project: The project whose data/ folder is replaced.
        log: Prints a progress line.

    Returns:
        The problems, as `load_problems` reads them back.

    Raises:
        ConfigError: The site no longer offers this repository's image.
    """
    images = api.images()
    image = next((i for i in images["images"] if i["id"] == IMAGE), None)
    if image is None:
        raise ConfigError(f"The site no longer offers the {IMAGE} image. Update this repository.")

    staging = project.data_dir / ".problems-new"
    empty_folder(staging)
    _download_problem_instances(staging, log)

    shutil.rmtree(project.problems_dir, ignore_errors=True)
    staging.rename(project.problems_dir)
    project.image_dir.mkdir(parents=True, exist_ok=True)
    (project.image_dir / "requirements.lock").write_text(image["lock"])
    (project.image_dir / "image.json").write_text(
        json.dumps(
            {
                "id": image["id"],
                "python_version": image["python_version"],
                "pull": image["pull"],
                "packages": image["packages"],
                "max_packages": images["requirements"]["max_packages"],
                "max_requirements_bytes": images["requirements"]["max_bytes"],
            },
            indent=2,
        )
    )
    return load_problems(project)


def load_problems(project: Project) -> list[Problem]:
    """Loads the problems and their public instances from the data/ folder.

    Args:
        project: The project whose data/ folder is read.

    Returns:
        The problems, sorted by slug.

    Raises:
        ConfigError: No problems have been downloaded yet.
    """
    if not project.problems_dir.is_dir():
        raise ConfigError("No problems downloaded yet. Run `uv run frontieror init` first.")

    problems = []
    for file in sorted(project.problems_dir.glob("*/problem.json")):
        folder = file.parent / "instances"

        meta = json.loads(file.read_text())
        index = json.loads((folder / "index.json").read_text())
        instances = [Instance(i["name"], folder / i["file"], int(i["time_limit_s"])) for i in index]
        problems.append(
            Problem(
                slug=meta["slug"],
                name=meta["name"],
                objective=meta["objective"],
                description=meta["description"],
                instance_schema=meta["instance_schema"],
                solution_schema=meta["solution_schema"],
                solution_json_schema=meta["solution_json_schema"],
                instances=instances,
            )
        )
    return problems


def load_image(project: Project) -> Image:
    """Loads the image's metadata and lock from data/image/.

    Args:
        project: The project whose data/ folder is read.

    Returns:
        The image.

    Raises:
        ConfigError: The image has not been downloaded yet.
    """
    meta_file = project.image_dir / "image.json"
    if not meta_file.is_file():
        raise ConfigError("The solver image is not downloaded yet. Run `uv run frontieror init` first.")

    meta = json.loads(meta_file.read_text())
    return Image(
        id=meta["id"],
        python_version=meta["python_version"],
        pull=meta["pull"],
        packages=meta["packages"],
        requirements_lock=(project.image_dir / "requirements.lock").read_text(),
        max_packages=meta["max_packages"],
        max_requirements_bytes=meta["max_requirements_bytes"],
    )


def _download_problem_instances(staging: Path, log: Callable[[str], None]) -> None:
    """Writes each problem's statement and public instances into staging/<slug>/."""
    for problem in api.problems():
        slug = _safe(problem["slug"])
        folder = staging / slug / "instances"
        folder.mkdir(parents=True)
        (staging / slug / "problem.json").write_text(
            json.dumps(
                {
                    "slug": slug,
                    "name": problem["name"],
                    "objective": problem["objective"],
                    "description": problem["description"],
                    "instance_schema": _as_text(problem["instance_schema"]),
                    "solution_schema": _as_text(problem["solution_schema"]),
                    "solution_json_schema": _as_text(problem["solution_json_schema"]),
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        index = []
        for instance in api.instances(slug):
            file = f"{_safe(instance['name'])}.json"
            (folder / file).write_bytes(api.instance_data(slug, instance["id"]))
            index.append(
                {"id": instance["id"], "name": instance["name"], "file": file, "time_limit_s": instance["time_limit_s"]}
            )

        (folder / "index.json").write_text(json.dumps(index, indent=2))
        log(f"  {slug}: {problem['name']} ({len(index)} public instance{'s' if len(index) != 1 else ''})")


def _as_text(schema: object) -> str:
    """The site sends a schema parsed when it is JSON, as its text otherwise; both are kept as text here."""
    return schema if isinstance(schema, str) else json.dumps(schema, indent=2, ensure_ascii=False)


def _safe(name: str) -> str:
    """A name from the site used as a file name: letters, digits, dot, dash and underscore only."""
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError(f"Unexpected name from the site: {name!r}")
    return name
