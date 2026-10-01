"""Small file helpers."""

import shutil
from pathlib import Path


def empty_folder(path: Path) -> Path:
    """Deletes `path` if it exists and creates it again, empty."""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path
