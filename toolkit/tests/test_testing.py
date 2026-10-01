"""The core of `frontieror test` and of what the agent's tools.test returns: run like the sandbox, check the format."""

from frontieror.tools import Tools

GOOD = """
def solve(instance, time_limit_s):
    return {"objective_value": 12.5, "routes": [{"vehicle": 0, "nodes": list(range(instance["n"]))}]}
"""


def tools(project, problem, runner):
    return Tools(project, problem, runner, log=lambda _: None)


def test_valid_solver_passes(project, problem, runner):
    report = tools(project, problem, runner).test(GOOD)
    assert report.passed
    assert report.results[0].objective == 12.5
    assert report.results[0].solution is not None


def test_failures_are_reported(project, problem, runner):
    t = tools(project, problem, runner)
    solve_cases = {
        "crash": "def solve(instance, time_limit_s):\n    raise RuntimeError('boom')",
        "timeout": "import time\n\ndef solve(instance, time_limit_s):\n    time.sleep(30)",
        # The solver's own folder is read-only, as in the sandbox.
        "crash-read-only": "def solve(instance, time_limit_s):\n    open('scratch.txt', 'w')",
        "invalid_format": GOOD.replace('list(range(instance["n"]))', '"0-1-2"'),
    }
    # Whole programs, without the data loader.
    program_cases = {
        "no_output": "pass",
        "invalid_json": "import sys\nopen(sys.argv[sys.argv.index('--output') + 1], 'w').write('{not json')",
    }
    results = {name: t.test(code).results[0] for name, code in solve_cases.items()}
    results |= {name: t.test(code, add_data_loader=False).results[0] for name, code in program_cases.items()}

    assert results["crash"].status == "crash" and "boom" in results["crash"].log
    assert results["timeout"].status == "timeout"
    assert results["no_output"].status == "no_output"
    assert results["crash-read-only"].status == "crash" and "Permission" in results["crash-read-only"].log
    assert (
        results["invalid_format"].status == "invalid_format" and "routes[0].nodes" in results["invalid_format"].message
    )
    assert results["invalid_json"].status == "invalid_json"
