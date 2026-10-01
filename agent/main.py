"""Your agent goes here. The toolkit calls `find_solver` once per problem, with the problem, the toolkit's tools and
`settings`, the [agent] table of frontieror.toml.

In this example, the agent is a simple loop that:
  1. asks the model for a program (and to extract one when the reply has no code block);
  2. runs it on the public instances;
  3. while something fails, sends the failures back and asks for a fix.

The challenge's checker is private, so an accurate score and feasibility only come from the site after you submit.
"""

from openrouter import OpenRouter, RetryConfig
from openrouter.utils.retries import BackoffStrategy

from agent import prompts
from frontieror import Problem, Tools

# Reads OPENROUTER_API_KEY
client = OpenRouter(
    timeout_ms=10 * 60_000,
    retry_config=RetryConfig("backoff", BackoffStrategy(1_000, 60_000, 1.5, 10 * 60_000), True, ["429", "5XX"]),
)


def find_solver(problem: Problem, tools: Tools, settings: dict) -> None:
    """Writes a solver for the problem and saves it with `tools.save`.

    Args:
        problem: The problem to solve.
        tools: The toolkit's tools, including `log`, `log_file`, `test`, and `save`.
        settings: The [agent] table from the frontieror.toml file, right now includes `model` and `max_attempts`.

    The function should call `tools.save(code)` with the final code to save it.
    """
    model = settings["model"]
    task = prompts.task(problem, tools.environment)
    messages = [{"role": "user", "content": task}]
    best_code, best_passed = None, -1

    for attempt in range(1, settings.get("max_attempts", 5) + 1):
        reply = ask(model, messages)
        tools.log_file(f"reply-{attempt}.md", reply)

        code = prompts.extract_code(reply)
        if code is None:
            feedback = prompts.NO_CODE_PROMPT
        else:
            report = tools.test(code)
            passed = len(report.results) - len(report.failures)
            tools.log(f"attempt {attempt}: {passed} of {len(report.results)} public instances passed")

            if passed > best_passed:
                best_code, best_passed = code, passed
            if report.passed:
                break

            feedback = prompts.fix(report.failures)
        # Each follow-up holds the task, the last reply and what went wrong with it.
        messages = [messages[0], {"role": "assistant", "content": reply}, {"role": "user", "content": feedback}]

    if best_code is None:
        tools.log("agent failed to find a working solver, so no solver was saved")
    else:
        tools.save(best_code)


def ask(model: str, messages: list[dict]) -> str:
    """The model's reply. Some reasoning models answer in `reasoning` and leave `content` empty."""
    message = client.chat.send(model=model, messages=messages).choices[0].message
    return message.content or message.reasoning or ""
