"""FrontierOR Challenge toolkit: tests solvers the way the sandbox runs them, and talks to the challenge site.

Your agent gets a `Problem` and `Tools` for each problem. The rest is for scripting what the `frontieror` command does:

    from frontieror import load_env, load_image, load_project, submit_main, wait_for_result

    project = load_project()
    load_env(project.root)
    wait_for_result(submit_main(project, load_image(project), yes=True))
"""

from importlib.metadata import version

from frontieror.api import ApiError, api
from frontieror.config import ConfigError, Project, load_env, load_project
from frontieror.data import Image, Instance, Problem, download_data, load_image, load_problems
from frontieror.environment import EnvironmentBuildError
from frontieror.submit import SubmitError, submit_main, submit_self_hosted, wait_for_result
from frontieror.testing import InstanceResult, Report, Runner
from frontieror.tools import Environment, Tools

__version__ = version("frontieror")

__all__ = [
    "ApiError",
    "ConfigError",
    "Environment",
    "EnvironmentBuildError",
    "Image",
    "Instance",
    "InstanceResult",
    "Problem",
    "Project",
    "Report",
    "Runner",
    "SubmitError",
    "Tools",
    "__version__",
    "api",
    "download_data",
    "load_env",
    "load_image",
    "load_problems",
    "load_project",
    "submit_main",
    "submit_self_hosted",
    "wait_for_result",
]
