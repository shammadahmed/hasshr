# Module owners

Fill in the names at kickoff. Every PR needs a review from the paired member.

| Module | Path | Owner | Name | Reviewer pair |
|---|---|---|---|---|
| Core agent, pipeline, contracts | `hasshr/agent.py`, `pipeline.py`, `agents/`, `contracts.py`, `prompts.py` | M1 (admin) | Hammad | M6 |
| Safety engine | `hasshr/safety/` | M2 | | M3 |
| Tools & platform | `hasshr/tools/`, `hasshr/env.py` | M3 | | M2 |
| Journal, undo & audit | `hasshr/journal/` | M4 | | M5 |
| Troubleshooting Cases | `hasshr/cases/` (new) | M5 | | M4 |
| CLI/UX, LLM layer, packaging | `hasshr/cli.py`, `hasshr/llm/`, `pyproject.toml` | M6 | | M1 |

## Stubs that must be replaced (keep the signatures)

| Stub | Replace with | Owner |
|---|---|---|
| `safety/classifier.py: classify()` | Pipeline-aware analyzer + `rules.yaml` | M2 |
| `safety/permissions.py: decide()` | Final permission-mode logic | M2 |
| `journal/store.py: Journal` | Backups, hashes, snapshots, rollback, redaction | M4 |
| `tools/files.py`, `tools/shell.py` | Full typed tools, `edit_file`, TTY-safe `sudo` | M3 |
| `llm/base.py: get_client()` | LiteLLM provider layer | M6 |
| `cli.py` | typer + rich UI | M6 |
