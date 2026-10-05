# TermiAI

Operate your computer in plain language, with safety controls you can trust.

```bash
pip install termiai
termiai "find my largest files"
termiai case new "nginx fails to start on boot" --test "systemctl is-active nginx"
termiai undo
```

---

## Key Capabilities

1. **Agent Loop (M1)**: Autonomous planning, step-by-step tool execution, and independent verification with read-only tools and replanning (up to 3 retries).
2. **Deterministic Safety Engine (M2)**: Multi-layer fail-closed classifier with shlex/AST inspection, hard-block detection (`rm -rf /`, `dd`, fork bombs, etc.), and granular permission modes (`ask-all`, `ask-sensitive`, `auto`, `paranoid`, `plan-only`). Irreversible actions always require approval.
3. **18 Typed Tools & Platform Adapters (M3)**: Native cross-platform operations (Linux, macOS, Windows) for file manipulation with Trash support, search, unified diff edits, system telemetry, log tailing, package state, service state, clipboard, web fetch, and safe single-boot kernel rollback (`grub-reboot`).
4. **Append-Only Journal, Snapshots & Undo (M4)**: Tamper-evident JSONL audit trails, automatic SHA-256 pre-execution backups, secret redaction (`api_key`, `token`, `password`), corrupted tail tolerance, and exact file/action rollbacks (`termiai undo`).
5. **System Troubleshooting Cases (M5)**: Structured problem-solving lifecycle:
   - Measurable success test definition and approval.
   - Strictly read-only diagnostic phase.
   - Ranked hypotheses with confidence labels (`CONFIRMED`, `LIKELY`, `SPECULATION`).
   - One-at-a-time trial execution with instant rollback on failure.
   - Clean-room confirmation (reverting winner and reapplying to ensure it alone resolves the issue).
   - Automated reboot survival and state preservation (`termiai case resume <case_id>`).
   - Distro kernel regression detection and single-boot recovery.
   - Markdown evidence reports generated at `~/.termiai/cases/<case_id>-report.md`.
6. **Rich CLI & Configuration (M6)**: Interactive streaming terminal UI, model provider integration via LiteLLM (`gpt-4o`, `claude-3-5-sonnet`, `gemini-1.5-pro`, local models via Ollama), and persistent configuration (`termiai config`).

---

## Installation & Setup

```bash
pip install termiai

# Configure your preferred LLM provider & API key
termiai config set model gpt-4o
termiai config set api_key sk-...

# Or configure local Ollama / Open-source models
termiai config set model ollama/llama3
```

---

## Usage

### 1. One-Shot & Interactive Assistant

```bash
# Execute a single natural language instruction
termiai "compress all pdf files in ~/Documents"

# Run in plan-only mode (inspect the plan without executing changes)
termiai "clean up old log files in /var/log" --plan-only

# Run in auto mode (safely auto-approves reversible actions)
termiai "organize my Downloads folder by file extension" --auto

# Interactive agent session
termiai
```

### 2. Troubleshooting Cases

```bash
# Start a new structured troubleshooting investigation
termiai case new "web server 502 bad gateway" --test "curl -f http://localhost:80"

# List past and ongoing troubleshooting cases
termiai case list

# Resume an investigation after a reboot or pause
termiai case resume <case_id>

# Undo all modifications performed during a case
termiai case undo <case_id>

# Monitor upstream issue trackers and distro package feeds
termiai case watch <case_id>
```

### 3. Journal & Rollback

```bash
# View recent execution history and audit entries
termiai history

# Undo the most recent reversible action
termiai undo

# Undo a specific journal entry by ID
termiai undo <entry_id>
```

---

## Architecture & Pipeline

Every tool call is strictly governed by the TermiAI pipeline:

```
User Prompt -> Planner -> Plan Steps -> Executor
                                           |
                                      Tool Call
                                           |
                              Deterministic Classifier (M2)
                                           |
                              Permission Mode Decision
                                           |
                       +-------------------+-------------------+
                       |                   |                   |
                    [ALLOW]              [ASK]              [REFUSE]
                       |                   |                   |
                       |             User Approves?          Aborted
                       |             (Yes/No/Edit)             |
                       +-------------------+                   |
                                           |                   |
                                      Snapshot &               |
                                     Journal Before            |
                                           |                   |
                                      Execute Tool             |
                                           |                   |
                                     Journal After &           |
                                     Redact Secrets            |
                                           |                   |
                                      Independent              |
                                     Verifier Check            |
                                           |                   |
                                      Plan Success?            |
                                      (or Replan)              |
```

---

## Development & Testing

```bash
git clone https://github.com/your-org/termiai.git
cd termiai
pip install -e ".[dev]"

# Run full test suite
pytest
```
