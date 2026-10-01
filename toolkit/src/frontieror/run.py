"""Runs a solver on one instance the way the sandbox does.

    python solve.py --problem <slug> --instance <instance> --output <solution> --time-limit <s>

from inside a read-only copy of the problem's folder, with the instance read-only, OMP_NUM_THREADS=2 and
SANDBOX_PROBLEM set, and stopped at the instance's time limit. A solver still running then fails, whatever it wrote.

Natively the solver runs as you on this machine, without the sandbox's isolation or memory limit; only a minimal
environment is passed. With Docker it runs in the published image under the sandbox's limits: 2 CPUs, 4 GB, no network,
a read-only filesystem with a /tmp.
"""

import contextlib
import os
import shutil
import signal
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from frontieror.constants import (
    DOCKER_START_GRACE_S,
    ENTRY,
    ENV_MOUNT,
    LOG_TAIL_BYTES,
    SANDBOX_CPUS,
    SANDBOX_MEMORY_MB,
    SANDBOX_PIDS,
    SANDBOX_TMP_SIZE,
)
from frontieror.environment import SolverEnv, docker_run
from frontieror.layout import solver_files

SANDBOX_PATH = f"{ENV_MOUNT}/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
SANDBOX_LIMITS = (
    f"--network none --cpus {SANDBOX_CPUS} --memory {SANDBOX_MEMORY_MB}m --memory-swap {SANDBOX_MEMORY_MB}m "
    f"--pids-limit {SANDBOX_PIDS} --read-only --tmpfs /tmp:rw,nosuid,size={SANDBOX_TMP_SIZE} --cap-drop ALL "
    "--security-opt no-new-privileges --ulimit nofile=1024:1024 --ulimit core=0 --init"
).split()


@dataclass(frozen=True)
class RunOutcome:
    status: str  # ok (exited with 0), crash or timeout.
    message: str
    wall_s: float
    log: str


@dataclass(frozen=True)
class Workspace:
    """One run's files: work/solver (read-only copy), work/in/instance.json, work/out, work/tmp, work/log.txt."""

    root: Path

    @property
    def solver(self) -> Path:
        return self.root / "solver"

    @property
    def instance(self) -> Path:
        return self.root / "in" / "instance.json"

    @property
    def output(self) -> Path:
        return self.root / "out" / "solution.json"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def log(self) -> Path:
        return self.root / "log.txt"

    @classmethod
    def create(cls, root: Path, solver_dir: Path, instance: Path) -> Workspace:
        """Copies the solver folder and the instance into a new workspace, ready to run.

        Use it with `with`, which removes it afterwards.
        """
        work = cls(root)
        work.tmp.mkdir(parents=True)
        work.output.parent.mkdir()
        work.instance.parent.mkdir()
        shutil.copyfile(instance, work.instance)

        for path in solver_files(solver_dir):
            target = work.solver / path.relative_to(solver_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

        _read_only(work.solver)
        _read_only(work.instance.parent)
        return work

    def remove(self) -> None:
        """Deletes the workspace, its read-only folders included."""
        for path in [self.root, *self.root.rglob("*")]:
            if path.is_dir() and not path.is_symlink():
                path.chmod(stat.S_IRWXU)
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *_: object) -> None:
        self.remove()


def run_native(work: Workspace, slug: str, time_limit_s: int, env: SolverEnv) -> RunOutcome:
    """Runs the solver natively, without Docker, mimicking the sandbox's environment and time limit."""
    variables = {
        "PATH": f"{env.python.parent}{os.pathsep}{os.environ.get('PATH', '')}",
        "VIRTUAL_ENV": str(env.venv),
        "HOME": str(work.tmp),
        "TMPDIR": str(work.tmp),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": str(SANDBOX_CPUS),
        "SANDBOX_PROBLEM": slug,
        "SANDBOX_TIMEOUT_S": str(time_limit_s),
    }
    command = [str(env.python), ENTRY, *_arguments(slug, work.instance, work.output, time_limit_s)]

    with work.log.open("wb") as log:
        start = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=work.solver,
            env=variables,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        try:
            code = process.wait(timeout=time_limit_s)
        except subprocess.TimeoutExpired:
            code = None
        finally:
            # Also stops what the solver left running, and the solver itself on Ctrl+C, which it does not receive.
            _kill_group(process)
        wall = time.monotonic() - start

    return _outcome(code, wall, time_limit_s, _sandbox_paths(_tail(work.log), work))


def run_docker(work: Workspace, slug: str, time_limit_s: int, image_ref: str, env_dir: Path | None) -> RunOutcome:
    """Runs the solver inside the docker image, mimicking the sandbox's environment and time limit."""
    name = f"frontieror-{uuid.uuid4().hex[:12]}"
    env = {
        "HOME": "/tmp",
        "SANDBOX_TIMEOUT_S": str(time_limit_s),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": str(SANDBOX_CPUS),
        "SANDBOX_PROBLEM": slug,
    }
    mounts = [
        f"{work.solver}:/work/solver:ro",
        f"{work.instance.parent}:/work/in:ro",
        f"{work.output.parent}:/work/out",
    ]

    if env_dir:
        mounts.append(f"{env_dir}:{ENV_MOUNT}:ro")
        env |= {"PATH": SANDBOX_PATH, "VIRTUAL_ENV": ENV_MOUNT}

    arguments = _arguments(slug, Path("/work/in/instance.json"), Path("/work/out/solution.json"), time_limit_s)
    command = docker_run(
        image_ref,
        ["python", ENTRY, *arguments],
        env=env,
        mounts=mounts,
        options=["--name", name, "--pull", "never", "-w", "/work/solver", *SANDBOX_LIMITS],
    )

    with work.log.open("wb") as log:
        start = time.monotonic()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)

        try:
            code = process.wait(timeout=time_limit_s + DOCKER_START_GRACE_S)
        except subprocess.TimeoutExpired:
            code = None
        finally:
            if process.poll() is None:  # Timed out, or interrupted by Ctrl+C.
                subprocess.run(["docker", "kill", name], capture_output=True)
                process.wait()
        wall = time.monotonic() - start

    return _outcome(code, wall, time_limit_s, _tail(work.log))


def _arguments(slug: str, instance: Path, output: Path, time_limit_s: int) -> list[str]:
    """Solver command line arguments, as the sandbox passes them."""
    return ["--problem", slug, "--instance", str(instance), "--output", str(output), "--time-limit", str(time_limit_s)]


def _outcome(code: int | None, wall: float, time_limit_s: int, log: str) -> RunOutcome:
    if code is None:
        return RunOutcome(
            "timeout",
            f"Execution timed out after {time_limit_s} s: the sandbox stops the solver "
            "at its time limit and scores the instance 0, even if it wrote a solution.",
            wall,
            log,
        )
    if code == 0:
        return RunOutcome("ok", "Exited normally", wall, log)
    if code < 0:
        return RunOutcome("crash", f"Killed by signal {-code}", wall, log)
    if code == 137:
        message = f"Killed (exit code 137), most likely for using more than {SANDBOX_MEMORY_MB} MB of memory"
        return RunOutcome("crash", message, wall, log)
    return RunOutcome("crash", f"Exited with code {code}", wall, log)


def _kill_group(process: subprocess.Popen) -> None:
    """Stops the solver and every process it started."""
    # No such group once all of them exited; macOS refuses a group of exited, unreaped processes.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _read_only(folder: Path) -> None:
    """Makes the folder and all its files read-only."""
    for path in [*folder.rglob("*"), folder]:
        if path.is_dir():
            path.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        else:
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _sandbox_paths(log: str, work: Workspace) -> str:
    """The log with this run's folder shown as the sandbox's /work, as a traceback in the sandbox would read."""
    for root in {str(work.root), str(work.root.resolve())}:
        log = log.replace(root, "/work")
    return log


def _tail(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > LOG_TAIL_BYTES:
        data = b"...\n" + data[-LOG_TAIL_BYTES:]
    return data.decode("utf-8", errors="replace")
