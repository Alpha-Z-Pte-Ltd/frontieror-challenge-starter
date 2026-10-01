# FrontierOR Challenge starter

A starting point for the [FrontierOR Challenge](https://frontieror-challenge.com): an example LLM agent that writes a
solver for each problem, and a toolkit that tests the solvers like the challenge's sandbox and submits them.

```bash
uv run frontieror init           # sign in, download the problems, set up the solver environment
uv run frontieror find-solvers   # your agent writes solvers/<slug>/solve.py for each problem
uv run frontieror test           # run the solvers on the public instances
uv run frontieror submit --wait  # submit and wait for the score
```

You need:

- [uv](https://docs.astral.sh/uv/)
- macOS or Linux (on Windows, use WSL)
- an account and a team on the [challenge site](https://frontieror-challenge.com)
- an [OpenRouter](https://openrouter.ai/keys) key for the example agent's model calls
- Docker, only for `--docker`

`uv run` installs everything else on first use, including Python 3.14.

## Getting started

Register on the [challenge site](https://frontieror-challenge.com), then run:

```bash
uv run frontieror init
```

It signs you in through the browser, downloads the problems and sets up the solver environment.

### Write your agent

Your agent lives in `agent/`. Start with `find_solver` in `agent/main.py`:

```python
def find_solver(problem: Problem, tools: Tools, settings: dict) -> None: ...
```

It gets a problem and has to produce solver code that defines:

```python
def solve(instance: dict, time_limit_s: int) -> dict: ...
```

Save that code with `tools.save(code)`. The toolkit adds the part that reads the instance, calls `solve` and writes
the solution.

The example `find_solver` asks a model for code, tests it, and asks for fixes until it works. Use it as a starting
point.

Run your agent on every problem:

```bash
uv run frontieror find-solvers
```

### Test and submit

Test your solvers on the public instances:

```bash
uv run frontieror test
```

Then submit them:

```bash
uv run frontieror submit --wait
```

`--wait` prints your score on the private instances once it's ready. Your team can submit 4 times per day.

## Project structure

| Path | What it is |
| --- | --- |
| `agent/` | Your agent. Start with `main.py`. |
| `solvers/` | One folder per problem with `solve.py`. This is what gets submitted. |
| `frontieror.toml` | Your agent's settings and extra PyPI packages. |
| `toolkit/` | The `frontieror` command. Don't change it. |

## Commands

- **`init`** signs in through the browser and saves an API token in `.env`, replacing any token you had (`--token`
  uses one you already have). It also asks for your OpenRouter key, downloads the problems into `data/` and builds the
  solver environment in `.envs/`. Run it again when a new stage starts.
- **`find-solvers [slug...] [--force] [--jobs N] [--docker]`** runs your agent on every problem without a solver.
  Logs and attempts go to `runs/<slug>/`. `--force` redoes problems that already have one; `--jobs` works on several
  at once.
- **`test [slug...] [--docker]`** runs each solver on the public instances and checks its output.
- **`submit [--yes] [--acknowledge-missing] [--wait]`** submits for your team's track:
  - Main track: uploads `solvers/` with your extra packages. The site runs them on the private instances.
  - Self-hosted track: runs your solvers here on the private instances and uploads the solutions, within a one-hour
    window.

## How solvers run

Like in the sandbox:

```bash
python solve.py --problem <slug> --instance <instance.json> --output <solution.json> --time-limit <seconds>
```

- Python 3.14 with numpy, scipy, pandas, networkx, ortools, pulp, highspy and pyomo, plus your extra packages.
- 2 CPUs, 4 GB of memory, no network. The solver's folder is read-only.
- A solver still running at the time limit scores 0. Leave a margin.
- The output follows the problem's solution schema, with a numeric `objective_value` that matches the decisions.

`test` only checks that the solver finishes in time and that its output has the right format. The checker is private,
so feasibility and the score come from `submit --wait`.

By default, solvers run directly on your machine, without the sandbox's isolation. `--docker` runs them in the
challenge's image with the sandbox's limits (on Apple Silicon under emulation, so timings are slower).

## Extra packages

Add PyPI packages to `frontieror.toml` (at most 50), then run `init` again:

```toml
[environment]
requirements = ["pyscipopt==6.2.1"]
```

They're sent with every Main-track submission.

## The prompt

`agent/prompts.py` adapts [FrontierOR](https://github.com/Minw913/FrontierOR)'s one-shot prompt to the challenge:
open-source solvers instead of Gurobi, 2 CPUs, and a `solve` function instead of a whole program.

## Developing the toolkit

```bash
uv run --all-packages pytest toolkit
uv run ruff format
uv run ruff check
```
