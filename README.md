# TermiAI

Operate your computer in plain language, with safety controls you can trust.

```
pip install termiai
termiai "find my largest files"
termiai
```

> Status: **walking skeleton (gate G1)**. The agent loop, execution pipeline and contracts are real and tested; safety, journal, tools, CLI and LLM layer are stubs owned by other members (see `OWNERS.md`).

## Try the skeleton (no API key)

```bash
pip install -e ".[dev]"
python -m termiai --mock "list my Downloads"
pytest -q
```

## How it works

```
prompt -> Planner -> steps -> Executor -> tool calls -> Pipeline -> Verifier -> report
                                                          |
         classify -> decide -> (ask user) -> journal.before -> tool -> journal.after
```

- **Planner** turns the request into plain-language steps (falls back to one step if the LLM output is malformed).
- **Executor** carries out each step with tool calls.
- **Pipeline** is the only way a tool can run: risk is classified by rules (the LLM's own rating can only raise it), the permission mode decides allow/ask/refuse, irreversible steps always ask, and everything is journaled and audited.
- **Verifier** checks the real system state using read-only tools and may trigger a replan (max 3 retries). An unparseable verdict counts as *not* verified.
- Limits: 25 Executor/Verifier LLM round-trips per run, 3 retries.

Permission modes: `ask-all`, `ask-sensitive` (default), `auto`. Hard-blocked commands are refused in every mode.

## Layout

```
termiai/
  contracts.py     shared data structures (M1; change only by agreement)
  pipeline.py      the execution pipeline (M1)
  agent.py         orchestrator: plan -> execute -> verify -> replan (M1)
  agents/          planner, executor, verifier (M1)
  prompts.py       system prompts (M1)
  safety/          classifier + permission modes (M2, stubs)
  tools/           typed tools + run_shell (M3, stubs)
  journal/         journal, undo, audit (M4, stub)
  llm/             provider layer (M6) + scripted LLM for tests/demo
  cli.py           command line (M6, minimal)
tests/
docs/KICKOFF.md    kickoff meeting agenda and decisions
```

See `CONTRIBUTING.md` for the working rules.
