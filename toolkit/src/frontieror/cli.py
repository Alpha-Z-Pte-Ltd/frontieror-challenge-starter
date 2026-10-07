"""The `frontieror` command: init, find-solvers, test and submit."""

import functools
import importlib
import os
import sys
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

import typer

from frontieror.api import ApiError, api
from frontieror.config import ConfigError, Project, load_env, load_project, save_env
from frontieror.constants import DEFAULT_ENTRY, ENTRY, TOKEN_ENV
from frontieror.data import Problem, download_data, load_image, load_problems
from frontieror.environment import EnvironmentBuildError, ensure_local
from frontieror.layout import check_layout
from frontieror.login import browser_login
from frontieror.submit import SubmitError, submit_main, submit_self_hosted, wait_for_result
from frontieror.testing import InstanceResult, Runner
from frontieror.tools import Tools

# Failures whose message tells the user what to do; they are shown as one red line instead of a traceback.
EXPECTED_ERRORS = (ConfigError, ApiError, EnvironmentBuildError, SubmitError, FileExistsError)

NATIVE_NOTE = (
    "Solvers run natively on this machine, as you and without the sandbox's isolation. "
    "Pass --docker to run them in the sandbox's image instead."
)

app = typer.Typer(
    help="FrontierOR Challenge toolkit: download the problems, write solvers with your agent, test them the way the "
    "sandbox runs them, and submit.",
    no_args_is_help=True,
    add_completion=False,
)

SlugsType = Annotated[
    list[str] | None, typer.Argument(help="Problem slugs; all problems of the stage when none are given.")
]
DockerType = Annotated[
    bool, typer.Option("--docker", help="Run solvers in the sandbox's image with its limits (needs Docker).")
]


def command(name: str | None = None) -> Callable[[Callable], Callable]:
    """Registers a command that shows expected failures as one red line and exits with 1, instead of a traceback."""

    def register(function: Callable) -> Callable:
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except EXPECTED_ERRORS as exc:
                _err(str(exc))
                raise typer.Exit(1) from None

        return app.command(name)(wrapper)

    return register


@command()
def init(
    token: Annotated[
        str | None, typer.Option(help="Use this API token instead of signing in through the browser.")
    ] = None,
) -> None:
    """Sign in, download the stage's problems and public instances, and build the local solver environment."""
    project = _load_project()
    me = _sign_in(project, token)
    _ask_openrouter_key(project)

    typer.echo(f"Signed in as {me['user']['name']} ({me['user']['email']}).")
    team, stage = me["team"], me["stage"]
    if team:
        typer.echo(f"Team: {team['name']}, {team['track']} track.")
    else:
        _warn("You are not on a team yet. Create or join one on the dashboard before you submit.")
    if not stage:
        _warn("No stage is open right now, so there are no problems to download.")
        return

    typer.echo(f"Downloading the problems of {stage['name']}...")
    problems = download_data(project, typer.echo)
    ensure_local(project, load_image(project), typer.echo)
    typer.echo(f"Ready: {len(problems)} problem(s) in data/problems. Next: `uv run frontieror find-solvers`.")


@command("find-solvers")
def find_solvers(
    slugs: SlugsType = None,
    force: Annotated[bool, typer.Option("--force", help="Also replace solvers that exist already.")] = False,
    jobs: Annotated[int, typer.Option(min=1, help="How many problems your agent works on at once.")] = 1,
    docker: DockerType = False,
) -> None:
    """Run your agent on each problem without a solver, writing solvers/<slug>/solve.py."""
    project = _load_project()
    todo = []
    for problem in _select(load_problems(project), slugs):
        if force or not _has_solver(project, problem.slug):
            todo.append(problem)
        else:
            typer.echo(f"[{problem.slug}] has a solver already; --force replaces it.")

    if not todo:
        return

    settings = dict(project.agent)
    entry = _load_entry(project, settings.pop("entry", DEFAULT_ENTRY))
    runner = _runner(project, docker)
    runner.setup()

    def work(problem: Problem) -> bool:
        """Runs the agent on one problem; a failure is reported and does not stop the others."""
        try:
            entry(problem, Tools(project, problem, runner, typer.echo), settings)
            return True
        except Exception as exc:
            log = project.runs_dir / problem.slug / "error.txt"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc())
            _err(f"[{problem.slug}] the agent failed: {exc} (traceback in {log.relative_to(project.root)})")
            return False

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        succeeded = list(pool.map(work, todo))

    written = sum(_has_solver(project, p.slug) for p in todo)
    typer.echo(f"{written} of {len(todo)} problem(s) have a solver now. Next: `uv run frontieror test`.")
    if not all(succeeded):
        raise typer.Exit(1)


@command()
def test(slugs: SlugsType = None, docker: DockerType = False) -> None:
    """Run solvers/<slug>/solve.py on the public instances the way the sandbox does, and check each output's format."""
    project = _load_project()
    problems = _select(load_problems(project), slugs)
    coverage = {c.slug: c for c in check_layout(project.solvers_dir, [p.slug for p in problems])}
    runner = _runner(project, docker)

    results: list[InstanceResult] = []
    bad_layout = False
    for problem in problems:
        layout = coverage[problem.slug]
        typer.secho(f"\n{problem.slug} · {problem.name}", bold=True)
        if layout.status != "ok":
            _warn(f"  {layout.message}")
            bad_layout = bad_layout or bool(slugs)  # A problem asked for by name must have a solver.
            continue

        for instance in problem.instances:
            keep = project.runs_dir / problem.slug / "test" / instance.name
            result = runner.run(project.solvers_dir / problem.slug, problem, instance, keep)
            results.append(result)
            _show(result, project)

    passed = sum(r.ok for r in results)
    typer.echo(
        f"\n{passed} of {len(results)} instance run(s) passed. Only the format is checked here; feasibility and the "
        "score come from the site after you submit.\n"
        "Next: `uv run frontieror submit --wait` to upload your solvers and solutions."
    )
    if passed < len(results) or bad_layout:
        raise typer.Exit(1)


@command()
def submit(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before submitting.")] = False,
    acknowledge_missing: Annotated[
        bool,
        typer.Option("--acknowledge-missing", help="Submit even when some problems have no solver (they score 0)."),
    ] = False,
    wait: Annotated[bool, typer.Option("--wait", help="Wait for the evaluation and show the result.")] = False,
) -> None:
    """Submit for your team's track: Main uploads the solvers; Self-hosted runs them here and uploads the solutions."""
    project = _load_project()
    me = api.me()
    team, stage = me["team"], me["stage"]
    if not team:
        raise SubmitError("Create or join a team on the dashboard first.")

    if not stage or stage["status"] != "active":
        raise SubmitError("Submissions are closed right now.")

    typer.echo(f"{team['name']}, {team['track']} track: {me['submissions_left_today']} submission(s) left today.")

    image = load_image(project)
    confirm = functools.partial(typer.confirm, default=False)
    if team["track"] == "main":
        submission_id = submit_main(
            project, image, acknowledge_missing=acknowledge_missing, yes=yes, log=typer.echo, confirm=confirm
        )
    else:
        runner = Runner(project, image, log=typer.echo)
        submission_id = submit_self_hosted(
            project, load_problems(project), runner, yes=yes, log=typer.echo, confirm=confirm
        )

    if wait:
        wait_for_result(submission_id, typer.echo)
    else:
        typer.echo("Follow it on the dashboard, or pass --wait next time.")


def _load_project() -> Project:
    """Finds the project and reads its .env into the environment."""
    project = load_project()
    load_env(project.root)
    return project


def _sign_in(project: Project, token: str | None) -> dict:
    """Checks the given or saved API token, signs in through the browser when none works, and saves the token."""
    if token:
        os.environ[TOKEN_ENV] = token

    if os.environ.get(TOKEN_ENV):
        try:
            me = api.me()
        except ApiError as exc:
            if exc.status != 401:
                raise
            _warn("The API token no longer works, so let's get a new one.")
        else:
            save_env(project.root, TOKEN_ENV, os.environ[TOKEN_ENV])
            return me

    typer.echo("Signing in through the browser. This makes a new API token, which replaces your current one.")
    save_env(project.root, TOKEN_ENV, browser_login(typer.echo))
    return api.me()


def _ask_openrouter_key(project: Project) -> None:
    """Asks for the example agent's OpenRouter key and saves it in .env, unless it is set or nobody can answer."""
    if os.environ.get("OPENROUTER_API_KEY") or not sys.stdin.isatty():
        return

    key = typer.prompt(
        "OpenRouter API key for your agent (https://openrouter.ai/keys, Enter to skip)",
        default="",
        show_default=False,
        hide_input=True,
    ).strip()
    if key:
        save_env(project.root, "OPENROUTER_API_KEY", key)


def _select(problems: list[Problem], slugs: list[str] | None) -> list[Problem]:
    """The problems named by `slugs`, or all of them when none are named."""
    if not slugs:
        return problems

    known = {p.slug: p for p in problems}
    unknown = [s for s in slugs if s not in known]
    if unknown:
        raise ConfigError(f"Unknown problem(s): {', '.join(unknown)}. Known: {', '.join(known) or 'none'}.")

    return [known[s] for s in slugs]


def _has_solver(project: Project, slug: str) -> bool:
    """Whether solvers/<slug>/solve.py exists."""
    return (project.solvers_dir / slug / ENTRY).exists()


def _load_entry(project: Project, spec: str) -> Callable:
    """The agent's function, named as `module:function`."""
    module_name, _, name = spec.partition(":")
    if str(project.root) not in sys.path:
        sys.path.insert(0, str(project.root))

    try:
        return getattr(importlib.import_module(module_name), name)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"Cannot load the agent entry {spec!r} from frontieror.toml: {exc}") from exc


def _runner(project: Project, docker: bool) -> Runner:
    """A runner for the project's image; warns when the solvers run natively."""
    image = load_image(project)
    if not docker:
        _warn(NATIVE_NOTE)
    return Runner(project, image, docker, typer.echo)


def _show(result: InstanceResult, project: Project) -> None:
    """Prints an instance's result; for a failure also its errors and, for a crash, the end of its output."""
    timing = (
        f"over {result.time_limit_s} s"
        if result.status == "timeout"
        else f"{result.wall_s:.1f} s of {result.time_limit_s} s"
    )
    if result.ok:
        objective = f"objective {result.objective:g} (not verified)"
        typer.secho(f"  ✓ {result.instance:<24} {timing:<18} {objective}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"  ✗ {result.instance:<24} {timing:<18} {result.status}", fg=typer.colors.RED)
        for line in result.message.splitlines()[:10]:
            typer.echo(f"      {line}")

        if result.status == "crash":
            tail = [line for line in result.log.strip().splitlines()[-8:] if line.strip()]
            if tail:
                typer.echo("      Output ends with:")
                for line in tail:
                    typer.echo(f"        {line}")

    if result.output_dir:
        typer.echo(f"      files: {result.output_dir.relative_to(project.root)}")


def _warn(message: str) -> None:
    """Prints a warning in yellow."""
    typer.secho(message, fg=typer.colors.YELLOW)


def _err(message: str) -> None:
    """Prints an error in red, to stderr."""
    typer.secho(message, fg=typer.colors.RED, err=True)
