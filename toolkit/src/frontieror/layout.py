"""The Main-track archive: one folder per problem, named by its slug, with the solver's entry point inside.

The problem folder holds solve.py, the only entry point the site runs.
A problem without a folder or without solve.py is labeled as missing, scoring 0 on all instances.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from frontieror.constants import ENTRY

IGNORED_NAMES = {"__pycache__", ".DS_Store", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc"}


@dataclass(frozen=True)
class Coverage:
    slug: str
    status: str  # ok, missing (no folder) or no_entrypoint (a folder without solve.py).

    @property
    def message(self) -> str:
        return {
            "ok": f"{self.slug}/{ENTRY}",
            "missing": f"no {self.slug}/ folder: its instances score 0",
            "no_entrypoint": f"{self.slug}/ has no {ENTRY}: its instances score 0",
        }[self.status]


def check_layout(solvers_dir: Path, slugs: list[str]) -> list[Coverage]:
    """Check that each problem folder exists and has its solve.py."""
    coverage = []
    for slug in slugs:
        folder = solvers_dir / slug
        if (folder / ENTRY).is_file():
            status = "ok"
        elif folder.is_dir():
            status = "no_entrypoint"
        else:
            status = "missing"
        coverage.append(Coverage(slug, status))
    return coverage


def solver_files(folder: Path) -> list[Path]:
    """The files of a solver folder that belong in the archive: no caches, no symbolic links."""
    files = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part in IGNORED_NAMES for part in relative.parts) or path.suffix in IGNORED_SUFFIXES:
            continue
        if path.is_symlink() or not path.is_file():
            continue

        files.append(path)

    return files


def pack(solvers_dir: Path, slugs: list[str]) -> bytes:
    """A zip with each given problem's folder at its root, as `<slug>/solve.py` and whatever else the folder holds."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for slug in slugs:
            for path in solver_files(solvers_dir / slug):
                archive.write(path, f"{slug}/{path.relative_to(solvers_dir / slug).as_posix()}")

    return buffer.getvalue()
