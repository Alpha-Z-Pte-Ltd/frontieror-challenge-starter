"""Where the project's files live, and what the user configured in frontieror.toml, .env and the environment."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv, set_key

from frontieror.constants import CONFIG_FILE, ENV_FILE


class ConfigError(Exception):
    """The project is set up wrongly; the message says how to fix it."""


@dataclass(frozen=True)
class Project:
    """The project's folders and its settings from frontieror.toml.

    Attributes:
        root: The folder that holds frontieror.toml.
        requirements: Extra PyPI packages for the solvers, from `[environment] requirements`.
        agent: The `[agent]` table; the CLI passes it, without `entry`, as `settings` to the agent.
    """

    root: Path
    requirements: list[str]
    agent: dict

    @property
    def data_dir(self) -> Path:
        """data/: the downloaded problems and image."""
        return self.root / "data"

    @property
    def problems_dir(self) -> Path:
        """data/problems/: one folder per problem, with its public instances."""
        return self.data_dir / "problems"

    @property
    def image_dir(self) -> Path:
        """data/image/: the image's metadata and requirements lock."""
        return self.data_dir / "image"

    @property
    def solvers_dir(self) -> Path:
        """solvers/: one folder per problem, with its solve.py."""
        return self.root / "solvers"

    @property
    def envs_dir(self) -> Path:
        """.envs/: the built solver environments."""
        return self.root / ".envs"

    @property
    def runs_dir(self) -> Path:
        """runs/: the agent's logs and the outputs of solver runs."""
        return self.root / "runs"


def find_root(start: Path | None = None) -> Path:
    """The nearest directory, from `start` up, that holds frontieror.toml."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / CONFIG_FILE).is_file():
            return directory

    raise ConfigError(
        f"No {CONFIG_FILE} here or in any parent directory. Run this inside the frontieror-starter repository."
    )


def load_project(start: Path | None = None) -> Project:
    """Finds the project and reads its frontieror.toml.

    Args:
        start: Where to start looking for frontieror.toml, going up; the current directory if None.

    Returns:
        The project.

    Raises:
        ConfigError: There is no frontieror.toml, or it is invalid.
    """
    root = find_root(start)
    try:
        config = tomllib.loads((root / CONFIG_FILE).read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILE} is not valid TOML: {exc}") from exc

    requirements = config.get("environment", {}).get("requirements", [])
    if not isinstance(requirements, list) or not all(isinstance(r, str) for r in requirements):
        raise ConfigError(f"[environment] requirements in {CONFIG_FILE} must be a list of strings.")

    agent = config.get("agent", {})
    if not isinstance(agent, dict):
        raise ConfigError(f"[agent] in {CONFIG_FILE} must be a table.")

    return Project(root=root, requirements=[r.strip() for r in requirements if r.strip()], agent=agent)


def load_env(root: Path) -> None:
    """Reads the project's .env into the environment; variables already set win, so a shell `export` overrides it.

    Args:
        root: The project's root folder, which holds .env.
    """
    load_dotenv(root / ENV_FILE)


def save_env(root: Path, key: str, value: str) -> None:
    """Sets one variable in .env and makes the file readable by its owner only."""
    path = root / ENV_FILE
    set_key(path, key, value)
    path.chmod(0o600)
    os.environ[key] = value
