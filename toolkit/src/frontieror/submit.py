"""Submitting. The Main track uploads the solvers as an archive with the image and extra packages as fields; the site
runs them on the private instances. The Self-hosted track runs the solvers here, on the private instances handed out
inside the site's submission window, and uploads each solution before its deadline.
"""

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frontieror.api import ApiError, api
from frontieror.checks import is_objective
from frontieror.config import Project
from frontieror.constants import (
    ENTRY,
    IMAGE,
    MAX_ARCHIVE_BYTES,
    MAX_UPLOAD_BYTES,
    SELF_HOSTED_ATTEMPTS,
    SELF_HOSTED_RETRY_S,
    SUBMISSION_POLL_RETRIES,
    SUBMISSION_POLL_S,
)
from frontieror.data import Image, Instance, Problem
from frontieror.layout import check_layout, pack
from frontieror.testing import Runner

_LINK = re.compile(r"@|://|[\\/]|^(?:git|hg|svn|bzr)\+|^[.~]")
_ARCHIVE = re.compile(r"\.(?:whl|zip|tgz|tbz|txz|tlz|tar(?:\.(?:gz|bz2|xz|zst|lz|lzma))?)$", re.IGNORECASE)

LogType = Callable[[str], None]
ConfirmType = Callable[[str], bool]


def _reject(question: str) -> bool:
    """The default ConfirmType, prints the question and answers False. Pass `yes=True` to override it."""
    print(f"{question} [Rejected; pass yes=True to confirm].")
    return False


class SubmitError(Exception):
    """Nothing was submitted; the message says why."""


def submit_main(
    project: Project,
    image: Image,
    *,
    acknowledge_missing: bool = False,
    yes: bool = False,
    log: LogType = print,
    confirm: ConfirmType = _reject,
) -> str:
    """Submits the solvers in solvers/<slug>/ for the Main track, with the image and extra packages.

    Args:
        project: The project, with its solvers and extra packages.
        image: The image the solvers run in.
        acknowledge_missing: Submit even when some problems have no solver; they score 0.
        yes: Submit without asking `confirm` first.
        log: Prints a progress line.
        confirm: Asks a yes-or-no question; the default answers no.

    Returns:
        The submission's id.

    Raises:
        SubmitError: Nothing was submitted, for the reason in the message.
        ApiError: The site refused the submission or could not be reached.
    """
    slugs = [p["slug"] for p in api.problems()]
    if not slugs:
        raise SubmitError("The open round has no problems to submit for.")

    coverage = check_layout(project.solvers_dir, slugs)
    for c in coverage:
        log(f"  {'✓' if c.status == 'ok' else '✗'} {c.message}")

    ready = [c.slug for c in coverage if c.status == "ok"]
    if not ready:
        raise SubmitError(
            "There is no solver for any problem of the open round. Run `uv run frontieror find-solvers` first."
        )

    missing = [c.slug for c in coverage if c.status != "ok"]
    if missing and not acknowledge_missing:
        if yes:
            raise SubmitError(
                f"No solver for {', '.join(missing)}. Pass --acknowledge-missing to submit anyway; "
                "those problems score 0."
            )

        if not confirm(f"No solver for {', '.join(missing)}: those problems score 0. Submit anyway?"):
            raise SubmitError("Nothing was submitted.")

    errors = _check_requirements(project.requirements, image)
    if errors:
        raise SubmitError("Fix [environment] requirements in frontieror.toml:\n" + "\n".join(errors))

    archive = pack(project.solvers_dir, ready)
    if len(archive) > MAX_ARCHIVE_BYTES:
        limit = MAX_ARCHIVE_BYTES // 1024 // 1024
        raise SubmitError(f"The archive has {len(archive) / 1024 / 1024:.1f} MB; the limit is {limit} MB.")

    extra = f" with {len(project.requirements)} extra package(s)" if project.requirements else ""
    if not yes and not confirm(f"Submit {len(ready)} solver(s) to run in {IMAGE}{extra}?"):
        raise SubmitError("Nothing was submitted.")

    result = api.submit_main(archive, IMAGE, "\n".join(project.requirements) or None, acknowledge_missing=bool(missing))
    log(f"Submitted {result['id']}. The site builds the environment, then runs every solver on the private instances.")
    if result.get("progress"):
        log(_progress(result["status"], result["progress"]))
    return result["id"]


def submit_self_hosted(
    project: Project,
    problems: list[Problem],
    runner: Runner,
    *,
    yes: bool = False,
    log: LogType = print,
    confirm: ConfirmType = _reject,
) -> str:
    """Runs the solvers here on the Self-hosted track's private instances and uploads their solutions.

    Opens the round's submission window, or resumes an open one. Each instance's clock starts when it is downloaded,
    and its solution is uploaded before its deadline. An instance the site refuses is skipped, and the rest still run.

    Args:
        project: The project, with its solvers.
        problems: The round's problems, as downloaded by `frontieror init`.
        runner: Runs the solvers.
        yes: Open a window without asking `confirm` first.
        log: Prints a progress line.
        confirm: Asks a yes-or-no question; the default answers no.

    Returns:
        The submission's id.

    Raises:
        SubmitError: No window was opened, for the reason in the message.
        ApiError: The site refused to open or finish the submission, or could not be reached.
    """
    submission = api.current_self_hosted()
    if submission:
        log(f"Resuming the open submission {submission['id']}, which closes at {submission['expires_at']}.")
    else:
        if not yes:
            window = (api.me().get("round") or {}).get("self_hosted_window_s")
            open_for = f" for {window // 60} minutes" if window else ""
            if not confirm(
                f"Start a Self-hosted submission? It stays open{open_for}, and each instance's clock starts when it is "
                "downloaded. This counts as one of today's submissions."
            ):
                raise SubmitError("Nothing was submitted.")

        submission = api.start_self_hosted()
        log(f"Started submission {submission['id']}; it closes at {submission['expires_at']}.")

    local = {p.slug: p for p in problems}
    folder = project.runs_dir / "self-hosted" / submission["id"]
    closed = False
    for instance in submission["instances"]:
        label = f"{instance['problem_slug']}/{instance['name']}"
        if instance["submitted_at"]:
            log(f"  {label}: already uploaded")
            continue

        if instance["upload_deadline"] and datetime.fromisoformat(instance["upload_deadline"]) <= datetime.now(UTC):
            log(f"  {label}: its time is over")
            continue

        slug = instance["problem_slug"]
        if not (project.solvers_dir / slug / ENTRY).is_file() or slug not in local:
            log(f"  {label}: no solver, so it is not started and scores 0")
            continue

        try:
            closed = _solve_instance(submission["id"], instance, project, local[slug], runner, folder, log)
        except ApiError as exc:
            log(f"  {label}: {exc}")
        if closed:
            break

    if not closed:
        api.finish_self_hosted(submission["id"])
    log("The submission is closed; the site checks the uploaded solutions now.")
    return submission["id"]


def wait_for_result(submission_id: str, log: LogType = print) -> dict:
    """Polls a submission until it is evaluated, logging a timed line whenever its progress changes, then logs its score
    per problem and instance, as the dashboard shows.

    Args:
        submission_id: The id `submit_main` or `submit_self_hosted` returned.
        log: Prints a progress line.

    Returns:
        The evaluated submission, as GET /api/v1/submissions/{id} returns it.

    Raises:
        ApiError: The site refused the request, or failed SUBMISSION_POLL_RETRIES times in a row.
    """
    line = None
    failures = 0
    while True:
        try:
            submission = api.submission(submission_id)
        except ApiError as exc:
            failures += 1
            if not exc.transient or failures > SUBMISSION_POLL_RETRIES:
                raise

            log(f"Could not get the status, trying again: {exc}")
            time.sleep(SUBMISSION_POLL_S)
            continue
        failures = 0

        status = submission["status"]
        progress = _progress(status, submission.get("progress"))
        if progress != line:
            line = progress
            log(f"{datetime.now():%H:%M:%S}  {line}")
        if status in ("completed", "failed"):
            break

        time.sleep(SUBMISSION_POLL_S)

    score = submission["score"]
    log(f"Score: {score:.4f}" if score is not None else "Score: none")
    if submission["error"]:
        log(f"Error: {submission['error']}")

    environment = submission.get("environment")
    if environment and environment.get("build_log"):
        log("The extra packages could not be installed. Build log:")
        log(environment["build_log"])

    for problem in submission["problems"]:
        problem_score = f"{problem['score']:.4f}" if problem["score"] is not None else "none"
        log(f"  {problem['slug']}: {problem_score}")
        for instance in problem["instances"]:
            result = instance["error"] or (
                f"score {instance['score']:.4f}" if instance["score"] is not None else "no score"
            )
            log(f"    {instance['name']} ({instance['set']}): {result}")
    return submission


def _progress(status: str, progress: dict | None) -> str:
    """How far a submission has got, in the dashboard's words: "Queued · 2 ahead · results in about 14 min"."""
    if not progress:
        return {"pending": "Queued"}.get(status, status.capitalize())

    if progress["instances_total"]:
        where = f"Running · {progress['instances_done']}/{progress['instances_total']} instances"
    elif status == "pending" and progress["ahead"]:
        ahead = progress["ahead"]
        where = f"Queued · {ahead} submission{'s' if ahead > 1 else ''} ahead"
    else:
        where = "Starting"
    return f"{where} · results in about {progress['results_in_minutes']} min"


def _solve_instance(
    submission_id: str, instance: dict, project: Project, problem: Problem, runner: Runner, folder: Path, log: LogType
) -> bool:
    """Downloads an instance, runs its solver and uploads the solution; True if the upload closed the submission."""
    label = f"{problem.slug}/{instance['name']}"
    path = folder / f"{problem.slug}-{instance['name']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_retrying(api.self_hosted_instance, submission_id, instance["id"], label=label, log=log))
    result = runner.run(
        project.solvers_dir / problem.slug,
        problem,
        Instance(instance["name"], path, instance["time_limit_s"]),
        keep=folder / problem.slug / instance["name"],
    )
    solution = result.solution.read_bytes() if result.solution else None
    if solution is not None and len(solution) > MAX_UPLOAD_BYTES:
        limit = MAX_UPLOAD_BYTES // 1024 // 1024
        log(f"  {label}: the solution has {len(solution) / 1024 / 1024:.1f} MB; the site takes at most {limit} MB.")
        return False

    if not _uploadable(solution):
        log(f"  {label}: {result.status}, nothing to upload. {result.message}")
        return False

    response = _retrying(api.upload_solution, submission_id, instance["id"], solution, label=label, log=log)
    note = "" if result.ok else f" (the local check found: {result.message.splitlines()[0]})"
    log(f"  {label}: uploaded, objective {json.loads(solution)['objective_value']}{note}")
    return response["submission_closed"]


def _retrying(request: Callable[..., Any], *args: Any, label: str, log: LogType) -> Any:
    """Calls `request(*args)`, trying again a few times when the site cannot be reached or fails on its side."""
    for attempt in range(1, SELF_HOSTED_ATTEMPTS + 1):
        try:
            return request(*args)
        except ApiError as exc:
            if not exc.transient or attempt == SELF_HOSTED_ATTEMPTS:
                raise
            log(f"  {label}: {exc}; trying again in {SELF_HOSTED_RETRY_S} s")
            time.sleep(SELF_HOSTED_RETRY_S)


def _uploadable(solution: bytes | None) -> bool:
    """Whether the site would take the file: a JSON object with a numeric objective_value."""
    if solution is None:
        return False

    try:
        data = json.loads(solution)
    except ValueError:
        return False

    return isinstance(data, dict) and is_objective(data.get("objective_value"))


def _check_requirements(requirements: list[str], image: Image) -> list[str]:
    """Check compatibility of the requirements with the submission rules."""
    errors = []
    if len(requirements) > image.max_packages:
        errors.append(f"{len(requirements)} extra packages; the limit is {image.max_packages}.")
    if len("\n".join(requirements).encode()) > image.max_requirements_bytes:
        errors.append(f"The list is longer than {image.max_requirements_bytes // 1024} KB.")

    for line in requirements:
        package = line.split(";", 1)[0].strip()
        if line.startswith("-"):
            errors.append(f"{line}: options such as -i, -e or -r are not accepted; packages come from PyPI.")
        elif line.endswith("\\"):
            errors.append(f"{line}: line continuations are not accepted.")
        elif _LINK.search(package) or _ARCHIVE.search(package):
            errors.append(f"{line}: links, paths and archive files are not accepted; use the package's name on PyPI.")

    return errors
