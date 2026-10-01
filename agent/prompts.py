"""Prompt templates for the agent.

Prompts follow from FrontierOR's one-shot evaluation, but ask for a `solve` function instead of a whole program.
"""

# ruff: noqa: E501 - the prompts have long lines

import re

from frontieror import Environment, InstanceResult, Problem

TASK_PROMPT = """\
# Problem Description

{description}

# Instance Data Schema (instance_schema.json)

{instance_schema}

# Solution Output Schema (solution_schema.json)

{solution_schema}

## Your Task
Based on the problem description, instance data schema, and solution output schema above, write a Python function `solve(instance, time_limit_s)` that solves this optimization problem as efficiently as possible, while aiming to achieve an objective value as close to optimal as you can.
The instance data schema (instance_schema.json) specifies the type, shape, and meaning of every field in the input JSON. Your function must be able to solve any instance following this schema.
The solution output schema (solution_schema.json) specifies the type, key format, and meaning of every field your output JSON must contain. Your output MUST strictly follow this schema: include "objective_value" and all decision variable fields using the exact field names, key naming conventions, and nesting structure defined in the schema.

### Implementation Requirements
1. Consider the tradeoff between solution quality against computational efficiency based on the nature of the problem. You can use any solution approach and any internal formulation you find effective.
2. The solution output schema describes the output format only. Your final output must project your solution onto the fields defined in solution_schema.json; any internal auxiliary variables should not appear in the output.
3. Use only the Python standard library and these pre-installed packages: {packages}. Gurobi, CPLEX and other commercial solvers are not installed.
4. If you use a MIP/LP/CP solver, set its time limit below `time_limit_s` and let it use at most 2 threads.
5. Your program will run in a containerized environment with Python {python_version}, restricted to **2 CPU cores** and 4 GB of memory, without network access.

### Time Limit Requirement
1. `solve` must return within `time_limit_s` seconds. The program is stopped when the time limit is reached and then scores zero, so return with a safety margin.
2. If the algorithm cannot find an optimal solution within this time limit, it must terminate and return the best feasible solution found so far.

### Output Format and Structure
1. Define `solve(instance: dict, time_limit_s: int) -> dict`. `instance` is the parsed instance JSON; return the solution as a dict following the solution output schema. It MUST NOT hard-code or embed instance data.
2. Do not parse command-line arguments, read the instance file or write the solution: a fixed `__main__` block appended to your code does that. Do not add your own `if __name__ == "__main__":` block.
3. The code's directory is read-only; write temporary files only through Python's `tempfile` module. The reported "objective_value" is recomputed from your decision variables when the solution is checked, and a solution whose value disagrees is rejected.
4. Output only the Python code (`solve` and anything it needs), enclosed in a single ```python ... ``` block.
"""

FIX_PROMPT = """\
Your previous code produced the following issues:

{issues}

Please fix the code and return the corrected version in a single ```python ... ``` block.
"""

NO_CODE_PROMPT = (
    "Your previous response did not contain a valid fenced Python code block. Extract the complete Python program "
    "from that response and return exactly one fenced python code block. Do not explain, do not summarize, and do "
    "not change the algorithm."
)


def task(problem: Problem, environment: Environment) -> str:
    """Construct the task prompt for the model, given the problem and the environment."""
    return TASK_PROMPT.format(
        description=problem.description,
        instance_schema=problem.instance_schema,
        solution_schema=problem.solution_schema,
        packages=", ".join(environment.packages),
        python_version=environment.python_version,
    )


def fix(failures: list[InstanceResult]) -> str:
    """Construct the prompt to fix the failed instances: each one's error and, for a crash, the end of its output."""
    issues = []
    for result in failures:
        issue = f"Instance {result.instance}: {result.message[:500]}"
        if result.status == "crash" and result.log.strip():
            issue += f"\nThe output ends with:\n{result.log.strip()[-500:]}"
        issues.append(issue)
    return FIX_PROMPT.format(issues="\n\n".join(issues))


def extract_code(reply: str) -> str | None:
    """The first ```python block of a reply."""
    match = re.search(r"```python\s*\n(.*?)```", reply, re.DOTALL)
    return match.group(1).strip() if match else None
