"""Fixed values of the site, the sandbox and this project."""

# The site.
DEFAULT_API_URL = "https://frontieror-challenge.com"
# The environment variable that holds the user's API token.
TOKEN_ENV = "FRONTIEROR_TOKEN"
CLI_CLIENT_ID = "frontieror-cli"
# How much longer to wait between sign-in polls when the site answers `slow_down` (RFC 8628).
SLOW_DOWN_S = 5
# Every submission from this repository runs in this image.
IMAGE = "python-3.14"
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
SUBMISSION_POLL_S = 10
# How many failed polls in a row `wait_for_result` retries before giving up.
SUBMISSION_POLL_RETRIES = 30
# A Self-hosted download or upload that fails on a network or server error is tried this often, this far apart.
SELF_HOSTED_ATTEMPTS = 3
SELF_HOSTED_RETRY_S = 5
# The largest Self-hosted solution the site takes.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# A problem folder holds solve.py, or an executable solve, never both.
ENTRY = "solve.py"
OTHER_ENTRY = "solve"

# The sandbox a solver runs in.
SANDBOX_CPUS = 2
SANDBOX_MEMORY_MB = 4096
SANDBOX_PIDS = 256
SANDBOX_TMP_SIZE = "64m"
MAX_SOLUTION_BYTES = 16 * 1024 * 1024
# How many format errors of a solution are reported at once, and how long each may be.
MAX_ERRORS = 20
MAX_ERROR_CHARS = 200
LOG_TAIL_BYTES = 16 * 1024
# Where the image keeps its lock, and where a built environment is mounted.
IMAGE_LOCK = "/opt/python-base/requirements.lock"
ENV_MOUNT = "/opt/env"
# Docker needs a moment to start a container, which the sandbox does not count against the time limit.
DOCKER_START_GRACE_S = 5

# This project.
CONFIG_FILE = "frontieror.toml"
ENV_FILE = ".env"
DEFAULT_ENTRY = "agent:find_solver"
