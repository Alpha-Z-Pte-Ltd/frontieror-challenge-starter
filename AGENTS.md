# AGENTS.md

This is a starter for the FrontierOR Challenge: an LLM agent writes a solver (`solvers/<slug>/solve.py`, defining
`solve(instance, time_limit_s) -> dict`) for each optimization problem, and the `frontieror` toolkit tests and submits
those solvers. The agent's entrypoint is `find_solver(problem, tools, settings)` in `agent/main.py`; it must save the
final code with `tools.save(code)`, which adds data loading functions. See https://frontieror-challenge.com/llms.txt
for the rules, problem format and sandbox details.

Use `uv` for everything: `uv run frontieror init` (sign in - requires user action, download problems into `data/`,
build the solver environment), `uv run frontieror find-solvers` (run the agent), `uv run frontieror test` (check
solvers on public instances) and `uv run frontieror submit --wait`. `frontieror.toml` configures the run: `[agent]`
sets the entrypoint and the `settings` passed to the entrypoint, and `[environment] requirements` lists extra PyPI
packages for solvers (rerun `init` after changing them).

Work in `agent/` and `solvers/`; treat `toolkit/` as fixed. Solvers run with Python 3.14, 2 CPUs, 4 GB of memory and no
network, and must finish within the time limit with a margin. `test` checks only timing and output format;
feasibility and score come from `submit --wait`, which is limited per team per day, so don't submit without being
asked. For formatting run `uv run ruff format`.
