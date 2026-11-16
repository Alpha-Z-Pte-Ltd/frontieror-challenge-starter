# AGENTS.md

This is a starter for the FrontierOR Challenge: an LLM agent writes an algorithm (`algorithms/<slug>/solve.py`,
defining `solve(instance, time_limit_s) -> dict`) for each optimization problem, and the `frontieror` toolkit tests and
submits those algorithms. The agent's entrypoint is `find_algorithm(problem, tools, settings)` in `agent/main.py`; it
must save the final code with `tools.save(code)`, which adds instance loading functions. See
https://frontieror-challenge.com/llms.txt for the rules, problem format and sandbox details.

Use `uv` for everything: `uv run frontieror init` (sign in - requires user action, download problems into `data/`,
build the algorithm environment), `uv run frontieror find-algorithms` (run the agent), `uv run frontieror test` (check
algorithms on public instances) and `uv run frontieror submit --wait`. `frontieror.toml` configures the run: `[agent]`
sets the entrypoint and the `settings` passed to the entrypoint, and `[environment] requirements` lists extra PyPI
packages for algorithms (rerun `init` after changing them).

Work in `agent/` and `algorithms/`; treat `toolkit/` as fixed. Algorithms run with Python 3.14, 2 CPUs, 4 GB of memory
and no network, and must finish within the time limit with a margin. `test` checks timing, output format and, with the
problem's published checker (`data/problems/<slug>/checker/feasibility_check.py`), feasibility; the score comes from
`submit --wait`, which is limited per team per day, so don't submit without being asked. For formatting run
`uv run ruff format`.
