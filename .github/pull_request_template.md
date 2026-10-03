## What & why
<!-- One or two sentences. Link the PRD requirement IDs (e.g. F-16, F-17). -->

## Module / owner
<!-- M1 core · M2 safety · M3 tools · M4 journal · M5 cases · M6 CLI/LLM -->

## Checklist (Definition of Done)
- [ ] Tests written and passing (`pytest -q`), `ruff check .` and `ruff format --check .` clean
- [ ] Anything that touches the system was tested **in the VM**, not on my laptop
- [ ] No tool bypasses `Pipeline.execute()` (classify -> decide -> ask -> journal -> tool)
- [ ] `contracts.py` unchanged, or the change was announced and approved by M1
- [ ] No secrets in code, logs, or test fixtures; sudo passwords never reach the LLM
- [ ] Short docstring/README note on usage and limits
- [ ] Demo A / Demo B still work

## Reviewer (paired member)
<!-- M2<->M3, M4<->M5, M1<->M6 -->
