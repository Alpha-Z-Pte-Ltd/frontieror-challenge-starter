"""The solver environment, built the way the sandbox builds a submission's environment.

- without extra packages, the image's lock is installed as it is;
- with them, the list is merged with the image's default packages (a listed package replaces the image's line for it)
  and resolved with `uv pip compile`, preferring the image's locked versions wherever they still fit, then installed
  with `uv pip sync`.

Natively this makes a virtualenv under .envs/ for this machine. The lock is for linux/amd64, so a package without a
wheel for this machine may fail here and still work in the sandbox; `--docker` builds in the image itself instead.
An environment is reused until the image's requirements lock or the list changes.
"""

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from frontieror.config import Project
from frontieror.constants import ENV_MOUNT, IMAGE_LOCK
from frontieror.data import Image
from frontieror.files import empty_folder

_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_READY = ".ready"


class EnvironmentBuildError(Exception):
    """The environment could not be built; the message has uv's output."""


@dataclass(frozen=True)
class SolverEnv:
    venv: Path
    python: Path
    requirements_lock: Path


def packages(image: Image, requirements: list[str]) -> list[str]:
    """What a solver can import, as the prompt names it."""
    return _merge(image.packages, requirements)


def ensure_local(project: Project, image: Image, log: Callable[[str], None] = print) -> SolverEnv:
    """The local environment built with uv, mimicking the sandbox's build job."""
    root = project.envs_dir / f"{image.id}-{_key(image, project.requirements)}"
    env = SolverEnv(
        venv=root / "venv", python=root / "venv" / "bin" / "python", requirements_lock=root / "requirements.lock"
    )
    if (root / _READY).exists():
        return env

    extra = f" and {len(project.requirements)} extra package(s)" if project.requirements else ""
    log(f"Building the solver environment: Python {image.python_version}, the {image.id} packages{extra}...")

    empty_folder(root)
    try:
        env.requirements_lock.write_text(image.requirements_lock)
        _uv("venv", str(env.venv), "--python", image.python_version)

        if project.requirements:
            requirements_in = root / "requirements.in"
            requirements_in.write_text("\n".join(_merge(image.packages, project.requirements)) + "\n")
            _uv(
                "pip",
                "compile",
                str(requirements_in),
                "--output-file",
                str(env.requirements_lock),
                "--python",
                str(env.python),
                "--no-header",
                "--no-annotate",
            )

        _uv("pip", "sync", str(env.requirements_lock), "--python", str(env.python), "--compile-bytecode")

    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise

    (root / _READY).touch()
    _remove_others(root)
    return env


def ensure_docker(project: Project, image: Image, image_ref: str, log: Callable[[str], None] = print) -> Path | None:
    """The environment for the extra packages, built inside the image as the sandbox does; None when there are none."""
    if not project.requirements:
        return None

    root = project.envs_dir / f"docker-{image.id}-{_key(image, project.requirements)}"
    env_dir = root / "env"
    if (root / _READY).exists():
        return env_dir

    log(f"Building the solver environment in {image_ref} with {len(project.requirements)} extra package(s)...")

    empty_folder(root)
    env_dir.mkdir()
    (root / "in").mkdir()
    (root / "in" / "requirements.in").write_text("\n".join(_merge(image.packages, project.requirements)) + "\n")

    script = " && ".join(
        [
            f"cp {IMAGE_LOCK} /work/out/requirements.lock",
            "uv pip compile /work/in/requirements.in --output-file /work/out/requirements.lock"
            " --python /usr/local/bin/python3 --no-header --no-annotate",
            f"uv venv {ENV_MOUNT} --python /usr/local/bin/python3 --allow-existing",
            f"uv pip sync /work/out/requirements.lock --python {ENV_MOUNT}/bin/python --compile-bytecode",
        ]
    )
    command = docker_run(
        image_ref,
        ["sh", "-c", script],
        env={"HOME": "/tmp", "UV_CACHE_DIR": "/tmp/uv-cache"},
        mounts=[f"{root / 'in'}:/work/in:ro", f"{root}:/work/out", f"{env_dir}:{ENV_MOUNT}"],
    )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise EnvironmentBuildError(
            f"The extra packages could not be installed in {image_ref}:\n{_tail(result.stderr)}"
        )

    (root / _READY).touch()
    _remove_others(root)
    return env_dir


def docker_run(
    image: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    mounts: Iterable[str] = (),
    options: Iterable[str] = (),
) -> list[str]:
    """A `docker run` command for the image's linux/amd64 build, run as this machine's user."""

    command = ["docker", "run", "--rm", "--platform", "linux/amd64", "--user", docker_user(), *options]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    for mount in mounts:
        command += ["-v", mount]

    return [*command, "--entrypoint", "", image, *args]


def docker_user() -> str:
    """The solver runs as this machine's user, so the files it writes to the mounted folders stay yours."""
    return f"{os.getuid()}:{os.getgid()}"


def _canonical(requirement: str) -> str:
    """The package name of a requirement line, normalised (PEP 503)."""
    match = _NAME.match(requirement)
    return re.sub(r"[-_.]+", "-", match[0]).lower() if match else requirement


def _merge(defaults: list[str], requirements: list[str]) -> list[str]:
    """The image's default packages with the listed ones on top: a listed package replaces the image's line for it."""
    listed = {_canonical(r) for r in requirements}
    return [d for d in defaults if _canonical(d) not in listed] + requirements


def _remove_others(keep: Path) -> None:
    """Environments of the same kind for an older lock or list, which nothing uses any more."""
    prefix = keep.name[: keep.name.rindex("-") + 1]
    for other in keep.parent.glob(f"{prefix}*"):
        if other != keep and other.is_dir():
            shutil.rmtree(other, ignore_errors=True)


def _key(image: Image, requirements: list[str]) -> str:
    """Get a unique key for the image and the extra packages, to name the environment folder."""
    digest = hashlib.sha256("\n".join([image.id, image.requirements_lock, *requirements]).encode())
    return digest.hexdigest()[:12]


def _uv(*args: str) -> None:
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise EnvironmentBuildError(
            "uv is not on PATH. Install it from https://docs.astral.sh/uv/ and run this with `uv run`."
        )

    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    result = subprocess.run([uv, *args, "--quiet"], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise EnvironmentBuildError(f"`uv {' '.join(args[:2])}` failed:\n{_tail(result.stderr)}")


def _tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])
