# Working rules

1. `main` is always runnable. Branch names: `m2/classifier-pipes`. Open a PR; the paired reviewer approves within 24 hours. No direct pushes to `main` after the walking skeleton (gate G1).
2. **Daily 10-minute standup:** done, doing, blocked. Blockers go to M1 the same day.
3. **Contracts** live in `hasshr/contracts.py`. Changes must be announced in the team chat and approved by M1.
4. **The pipeline rule:** every action runs through `Pipeline.execute()`. A tool that is called any other way is a bug.
5. **Test VM:** all `sudo`, `rm`, package, kernel and GRUB tests run in the shared Ubuntu VM image, never on a personal laptop. Reset the VM snapshot after destructive tests.
6. **Secrets:** never commit API keys. Use `.env` (git-ignored). `sudo` passwords go straight to the user's terminal, never through the LLM, logs, or tests.
7. **Definition of Done:** reviewed PR, tests passing in CI, behaviour verified in the VM if it touches the system, short docs, and Demo A/B still work.
8. **Replace stubs, keep signatures.** If a signature must change, that is a contract change (rule 3).

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q && ruff check . && ruff format --check .
python -m hasshr --mock "list my Downloads"      # walking skeleton, no API key needed
```
