"""Gets an API token through the browser, so nobody has to copy one by hand."""

import time
import webbrowser
from collections.abc import Callable

from frontieror.api import ApiError, api
from frontieror.constants import CLI_CLIENT_ID, SLOW_DOWN_S

EXPIRED = "The sign-in was not approved in time. Run frontieror init again."


def browser_login(log: Callable[[str], None] = print) -> str:
    """Asks the user to approve this sign-in on the site, and waits for the token, which replaces their old one."""
    started = api.device_code(CLI_CLIENT_ID)
    code = started["user_code"]
    log(f"Open {started['verification_uri_complete']}")
    log(f"and approve the code {code[:4]}-{code[4:]} while signed in.")
    webbrowser.open(started["verification_uri_complete"])

    interval = started["interval"]
    deadline = time.monotonic() + started["expires_in"]
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            return api.device_token(started["device_code"], CLI_CLIENT_ID)["access_token"]
        except ApiError as exc:
            if exc.code == "slow_down":
                interval += SLOW_DOWN_S
            elif exc.code != "authorization_pending":
                raise ApiError(exc.status, _explain(exc), exc.code) from None

    raise ApiError(400, EXPIRED, "expired_token")


def _explain(error: ApiError) -> str:
    if error.code == "access_denied":
        return "The sign-in was denied on the site."
    if error.code == "expired_token":
        return EXPIRED
    return f"The sign-in failed: {error}"
