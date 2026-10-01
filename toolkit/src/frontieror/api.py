"""Client for the challenge site's API (/api/v1), shared by the whole toolkit as `api`."""

import os
from functools import cached_property
from importlib.metadata import version
from typing import Any

import httpx

from frontieror.constants import DEFAULT_API_URL, TOKEN_ENV


class ApiError(Exception):
    """A refused or failed request.

    Attributes:
        status: The HTTP status, 0 when the site could not be reached.
        code: The `error` of a device-flow answer, such as `authorization_pending`; None for other endpoints.
    """

    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def transient(self) -> bool:
        """Whether trying again may help: the site could not be reached, or failed on its side."""
        return self.status == 0 or self.status >= 500

    @classmethod
    def from_response(cls, response: httpx.Response) -> ApiError:
        """Reads `{"error": message}`, or the device flow's `{"error": code, "error_description": message}`."""
        try:
            body = response.json()
        except ValueError:
            body = {}

        if not isinstance(body, dict):
            body = {}

        if "error_description" in body:
            return cls(response.status_code, body["error_description"], code=body.get("error"))
        return cls(response.status_code, body.get("error") or f"{response.status_code} {response.reason_phrase}")


class Api:
    """Client for the challenge site's API; use the shared `api` instance.

    The site's URL is FRONTIEROR_API_URL, or the challenge site when that is not set. Every request but the CLI
    sign-in sends the API token from FRONTIEROR_TOKEN. Both are read when needed, so values from .env apply once
    `load_env` has run. Every method raises ApiError when the site refuses the request or cannot be reached.
    """

    @property
    def base_url(self) -> str:
        """The site's URL, without a trailing slash."""
        return (os.environ.get("FRONTIEROR_API_URL") or DEFAULT_API_URL).rstrip("/")

    @cached_property
    def _client(self) -> httpx.Client:
        """The HTTP client, created on the first request."""
        return httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"User-Agent": f"frontieror-toolkit/{version('frontieror')}"},
            timeout=httpx.Timeout(60, read=300),
        )

    # The user, their team and the stage.

    def me(self) -> dict:
        """Returns the signed-in user, their team, the current stage and the submissions left today."""
        return self._json("GET", "/me")

    # Problems and public instances of the current stage.

    def problems(self) -> list[dict]:
        """Returns the problems of the current stage, with their descriptions and schemas."""
        return self._json("GET", "/problems", params={"details": "1"})

    def instances(self, slug: str) -> list[dict]:
        """Lists the public instances of a problem.

        Args:
            slug: The problem's slug.

        Returns:
            The instances, each with its `id`, `name` and `time_limit_s`.
        """
        return self._json("GET", f"/problems/{slug}/instances")

    def instance_data(self, slug: str, instance_id: str) -> bytes:
        """Downloads a public instance.

        Args:
            slug: The problem's slug.
            instance_id: The instance's id, from `instances`.

        Returns:
            The instance file, which is JSON.
        """
        return self._request("GET", f"/problems/{slug}/instances/{instance_id}").content

    def images(self) -> dict:
        """Returns the site's docker images, with their packages and locks, and the limits on extra packages."""
        return self._json("GET", "/images")

    # Submissions.

    def submit_main(
        self, archive: bytes, docker_image: str, requirements: str | None, acknowledge_missing: bool
    ) -> dict:
        """Uploads a Main-track submission.

        Args:
            archive: The zip with one folder per problem.
            docker_image: The image the solvers run in.
            requirements: Extra PyPI packages, one per line, or None.
            acknowledge_missing: Submit even though some problems have no solver.

        Returns:
            The new submission.
        """
        data = {"docker_image": docker_image}
        if requirements:
            data["requirements"] = requirements
        return self._json(
            "POST",
            "/main/submissions",
            params={"acknowledge_missing": "1"} if acknowledge_missing else None,
            files={"file": ("submission.zip", archive, "application/zip")},
            data=data,
        )

    def submission(self, submission_id: str) -> dict:
        """Returns a submission of the team, with its status and results.

        Args:
            submission_id: The submission's id.
        """
        return self._json("GET", f"/submissions/{submission_id}")

    def start_self_hosted(self) -> dict:
        """Opens a Self-hosted submission and its one-hour window; returns it with its instances."""
        return self._json("POST", "/self-hosted/submissions")

    def current_self_hosted(self) -> dict | None:
        """Returns the team's open Self-hosted submission, or None if there is none."""
        try:
            return self._json("GET", "/self-hosted/submissions/current")
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def self_hosted_instance(self, submission_id: str, instance_id: str) -> bytes:
        """Downloads a Self-hosted instance; the first call starts its clock.

        Args:
            submission_id: The open submission's id.
            instance_id: The instance's id, from the submission's `instances`.

        Returns:
            The instance file, which is JSON.
        """
        return self._request("POST", f"/self-hosted/submissions/{submission_id}/instances/{instance_id}").content

    def upload_solution(self, submission_id: str, instance_id: str, solution: bytes) -> dict:
        """Uploads the solution of a Self-hosted instance; the first upload is final.

        Args:
            submission_id: The open submission's id.
            instance_id: The instance's id.
            solution: The solution file, which is JSON.

        Returns:
            `{"submission_closed": bool}`, true when this upload closed the submission.
        """
        return self._json(
            "PUT",
            f"/self-hosted/submissions/{submission_id}/instances/{instance_id}/solution",
            content=solution,
            headers={"Content-Type": "application/json"},
        )

    def finish_self_hosted(self, submission_id: str) -> dict:
        """Closes a Self-hosted submission, which is then checked and scored.

        Args:
            submission_id: The open submission's id.

        Returns:
            `{"queued": bool}`, false when nothing was uploaded and the submission scores 0.
        """
        return self._json("POST", f"/self-hosted/submissions/{submission_id}/finish")

    # Signing in the CLI: the device authorization grant (RFC 8628), form-encoded as the RFC has it. No token needed.

    def device_code(self, client_id: str) -> dict:
        """Starts a browser sign-in.

        Args:
            client_id: The CLI's client id.

        Returns:
            The `device_code`, `user_code`, `verification_uri_complete`, `expires_in` and `interval`.
        """
        return self._json("POST", "/device/code", signed_in=False, data={"client_id": client_id})

    def device_token(self, device_code: str, client_id: str) -> dict:
        """Gets the token of a browser sign-in once the user has approved it.

        Args:
            device_code: The `device_code` from `device_code`.
            client_id: The CLI's client id.

        Returns:
            The new `access_token`, which replaces the user's previous one.
        """
        return self._json(
            "POST",
            "/device/token",
            signed_in=False,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": client_id,
            },
        )

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Sends a request and returns its JSON answer."""
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError:
            raise ApiError(
                response.status_code, f"{self.base_url} answered {path} with something other than JSON."
            ) from None

    def _request(self, method: str, path: str, *, signed_in: bool = True, **kwargs: Any) -> httpx.Response:
        """Sends a request, with the API token unless `signed_in` is False, and raises ApiError if it fails."""
        if signed_in:
            token = os.environ.get(TOKEN_ENV)
            if not token:
                raise ApiError(401, "No API token. Run `uv run frontieror init` first.")
            kwargs["headers"] = {**kwargs.get("headers", {}), "Authorization": f"Bearer {token}"}

        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"Could not reach {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise ApiError.from_response(response)
        return response


api = Api()
