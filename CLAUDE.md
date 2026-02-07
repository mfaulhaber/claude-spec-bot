# Claude Instructions (Project Operating Guide)

## Project Overview

This project is an autonomous Claude Code agent exposed over Slack. It has two components:

1. **Runner** (`src/poc/`) — A Python agent running inside Docker. Receives goals from the orchestrator, calls the Claude API, executes tools (bash, file I/O, search), and reports progress via HTTP callbacks. Listens on port 8000.
2. **Orchestrator** (`orchestrator_host/`) — A host-side Slack bot that connects to Slack via Socket Mode. Receives `!poc` commands, manages jobs, posts progress/approval messages, and bridges user interactions to the runner.

## Architecture

```
Slack (Socket Mode)
    │
    ▼
┌─────────────────────────────┐
│  Orchestrator (host)        │
│  orchestrator_host/         │
│  - Slack Bolt app           │
│  - Parses !poc commands     │
│  - Manages job state        │
│  - Callback server (:8001)  │
│    receives runner events   │
│  - Posts progress + approval│
│    messages to Slack thread │
│  - Loads tokens from        │
│    orchestrator_host/.env   │
└──────────┬──────────────────┘
           │ HTTP (localhost:8000)
           │  /jobs/{id}/start
           │  /jobs/{id}/approve
           │  /jobs/{id}/message
           │  /jobs/{id}/cancel
           │  /jobs/{id}/status
           ▼
┌─────────────────────────────┐
│  Runner (Docker :8000)      │
│  src/poc/                   │
│  - Agent loop: calls Claude │
│    API, dispatches tools    │
│  - Tools: bash, read_file,  │
│    write_file, edit_file,   │
│    list_files, search_files │
│  - POSTs events to callback │
│    URL (host:8001)          │
│  - Pauses on approval_needed│
│    resumes on /approve POST │
│  - Mounts:                  │
│    /workspace = repo root   │
│    /runner = state/logs     │
└─────────────────────────────┘
```

**Message flow**: Slack user sends `!poc run <task>` → Orchestrator creates job, POSTs to runner `/jobs/{id}/start` → Runner starts agent loop (Claude API + tool execution) → Runner POSTs events to orchestrator callback `:8001/events` → Orchestrator posts progress to Slack thread → When tool needs approval, runner pauses and orchestrator posts Block Kit buttons → User clicks Approve/Deny → Orchestrator edits the button message in place (replacing buttons with decision text) and POSTs to runner `/jobs/{id}/approve` → Agent continues or stops. If no approval is given within 10 minutes, the runner auto-denies the tool call and posts an `approval_timeout` event.

## Execution Boundary
- The runner (`src/poc/`) runs inside Docker. Start it with `docker compose up -d`.
- The orchestrator (`orchestrator_host/`) runs on the host. Start it with `python -m orchestrator_host.main`.
- Do NOT run runner commands directly on the host.
- Only mounted paths are:
  - `/workspace` = repo root
  - `/runner` = state/logs/artifacts (host-mounted `./runner`)
- Never request mounting `$HOME`, `~/.ssh`, `SSH_AUTH_SOCK`, `/var/run/docker.sock`, or any credential directories.

## Networking
- The runner exposes port 8000 (mapped to host `localhost:8000`).
- The orchestrator runs a callback server on port 8001 to receive runner events.
- The orchestrator communicates with the runner via HTTP at `http://localhost:8000`.
- The runner communicates back to the orchestrator via `http://host.docker.internal:8001/events`.
- Internet access is allowed for API calls (Claude API).
- Host Ollama is available at: `OLLAMA_BASE_URL=http://host.docker.internal:11434`

## Secrets
- Secrets must not be committed.
- Use `runner/.env` for:
  - `ANTHROPIC_API_KEY` (required for agent mode)
  - optionally `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`
- Use `orchestrator_host/.env` for (loaded automatically via python-dotenv):
  - `SLACK_BOT_TOKEN`
  - `SLACK_APP_TOKEN`
- If keys are missing, set state to BLOCKED and ask concise questions.

## State and Plan Files (must always be updated)
- `runner/state.json` is the source of truth. Update it on every phase boundary and after meaningful outputs.
- `runner/plan.md` should contain a human-readable checklist, assumptions, decisions, and commands used.
- Logs must be written under `runner/logs/`
- Reports/artifacts must be written under `runner/artifacts/`
- Per-job state is stored at `runner/jobs/<job_id>/state.json` (managed by orchestrator)
- Per-job conversation history is stored at `runner/jobs/<job_id>/conversation.json` (managed by runner)
- Per-job events are logged at `runner/jobs/<job_id>/events.jsonl` (managed by runner)

## Required Project Layout
- Docker:
  - `docker-compose.yml` — Defines the `runner` service, port mapping (8000:8000), volume mounts, env
  - `Dockerfile.poc` — Python 3.12-slim image with git/ripgrep, installs anthropic SDK, starts `python -m poc.handler`
- Runner (Docker-side):
  - `src/poc/__init__.py`
  - `src/poc/handler.py` — HTTP API handler with multi-endpoint routing (port 8000)
  - `src/poc/agent.py` — AgentSession class, core agent loop with approval workflow
  - `src/poc/claude_client.py` — Anthropic SDK wrapper with retry and token tracking
  - `src/poc/tools.py` — Tool schemas and executors (bash, file I/O, search)
  - `src/poc/callback.py` — HTTP callback client for posting events to orchestrator
  - `runner/.env.example` (NOT `.env`)
  - `runner/state.json`, `runner/plan.md`
  - `runner/logs/`, `runner/artifacts/`
  - `runner/jobs/` (per-job state, gitignored)
- Orchestrator (host-side):
  - `orchestrator_host/__init__.py`
  - `orchestrator_host/main.py` — Entry point, loads .env, starts callback server + Socket Mode
  - `orchestrator_host/slack_bot.py` — Slack Bolt app, `!poc` commands, Block Kit action handlers
  - `orchestrator_host/docker_exec.py` — HTTP client to runner (start, approve, message, cancel)
  - `orchestrator_host/state.py` — JobState dataclass with agent fields, file I/O with locking
  - `orchestrator_host/jobs.py` — Job queue, dispatches start/cancel to runner via HTTP
  - `orchestrator_host/callback_server.py` — HTTP event receiver on port 8001
  - `orchestrator_host/progress.py` — Maps runner events to Slack messages (throttled, Block Kit)
  - `orchestrator_host/approvals.py` — Tracks pending approvals, handles button clicks and text replies
  - `orchestrator_host/.env.example` (NOT `.env`)

## Package Layout
- `src/poc/` — Runner package (runs inside Docker, discovered via `PYTHONPATH=/workspace/src`)
- `orchestrator_host/` — Orchestrator package (installed on host via `pip install -e ".[orchestrator]"`)
- Both discovered by setuptools via `where = ["src", "."]` with `include = ["poc*", "orchestrator_host*"]`

## Build/Test Discipline
- Prefer `pyproject.toml` with a standard modern toolchain.
- Use `pytest` for tests.
- Use `ruff` for lint and format.
- Lint covers `src/`, `tests/`, and `orchestrator_host/`.
- Networked tests must be optional and skipped unless corresponding env vars are present.

## Verifying Changes
- **Always run unit tests before committing**:
  - `source .venv/bin/activate && python -m pytest tests/ -v`
- All 151+ tests must pass.
- Networked tests are skipped automatically when API keys are absent.
- **If any runner code changed** (`src/poc/`, `Dockerfile.poc`, `docker-compose.yml`):
  - Rebuild and restart the container: `docker compose up -d --build`
  - Verify the runner responds: `curl -s http://localhost:8000/health`

## Definition of Done (DoD)
- Unit tests pass: `python -m pytest tests/ -v`
- Lint passes: `ruff check src/ tests/ orchestrator_host/`
- `docker compose build` succeeds.
- `docker compose up -d` starts the runner and it responds on port 8000.
- `curl -s http://localhost:8000/health` returns `{"status": "ok", "service": "poc-runner"}`.

## How to run
- Run unit tests:
  - `source .venv/bin/activate && python -m pytest tests/ -v`
- Start the runner (Docker, persistent):
  - `docker compose up -d` — starts the HTTP handler on port 8000
  - `docker compose logs -f` — follow runner logs
  - `docker compose down` — stop the runner
- Start the Slack orchestrator (host-side):
  - `source .venv/bin/activate && python -m orchestrator_host.main`
  - Tokens are loaded automatically from `orchestrator_host/.env`
  - Callback server starts automatically on port 8001
- Test the runner health:
  - `curl -s http://localhost:8000/health`
- Test the agent directly (no Slack needed):
  - `curl -X POST http://localhost:8000/jobs/test-001/start -H "Content-Type: application/json" -d '{"goal": "List all Python files in the project", "callback_url": "http://host.docker.internal:8001/events"}'`
  - Check status: `curl -s http://localhost:8000/jobs/test-001/status`

## End-to-End Testing

### Without Slack (runner only)

1. Start the runner:
   ```bash
   docker compose up -d --build
   ```

2. Verify health:
   ```bash
   curl -s http://localhost:8000/health
   # {"status": "ok", "service": "poc-runner"}
   ```

3. Verify the Claude client can handle a job — submit a read-only task that
   uses only auto-approved tools. This confirms the API key is valid, the
   Claude client works, and the agent loop completes without approval:
   ```bash
   curl -s -X POST http://localhost:8000/jobs/smoke-test/start \
     -H "Content-Type: application/json" \
     -d '{"goal": "List all Python files in the project", "callback_url": ""}'
   ```

4. Poll status until `"status": "completed"`:
   ```bash
   curl -s http://localhost:8000/jobs/smoke-test/status
   # {"job_id": "smoke-test", "status": "completed", "iteration": 2, ...}
   ```
   If `status` is `failed`, check `result_text` for the error and runner
   logs via `docker compose logs --tail 50`.

5. Test the approval flow — start a task that needs bash (requires approval):
   ```bash
   curl -s -X POST http://localhost:8000/jobs/test-approve/start \
     -H "Content-Type: application/json" \
     -d '{"goal": "Run python --version and report the output", "callback_url": ""}'
   ```

6. Poll until `waiting_approval`, then approve using the `tool_use_id` from
   the `pending_approval` field in the status response:
   ```bash
   curl -s http://localhost:8000/jobs/test-approve/status
   # {"status": "waiting_approval", "pending_approval": {"tool_use_id": "toolu_abc...", ...}}
   curl -X POST http://localhost:8000/jobs/test-approve/approve \
     -H "Content-Type: application/json" \
     -d '{"tool_use_id": "<from status response>", "approved": true}'
   ```

7. To test approval timeout, start a job that triggers approval and don't
   respond. After 10 minutes (600s) the tool call is automatically denied
   and the agent continues. The runner posts an `approval_timeout` event to
   the callback URL. The timeout is configurable via the `approval_timeout`
   field in `AgentSession` (seconds).

### With Slack (full end-to-end)

1. Ensure `runner/.env` has `ANTHROPIC_API_KEY` set.
2. Ensure `orchestrator_host/.env` has `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`.
3. Start the runner: `docker compose up -d --build`
4. Start the orchestrator: `source .venv/bin/activate && python -m orchestrator_host.main`
5. In Slack, type: `!poc run List all Python files`
6. The bot should:
   - Confirm the job was started
   - Post thinking/progress updates
   - Post tool call summaries (e.g., `:gear: list_files: *.py`)
   - Complete with a summary message
7. For tasks that involve writes or bash commands, the bot will post approval
   buttons. Click **Approve**, **Approve All**, or **Deny**, or reply in the
   thread with "yes"/"no".
   - After clicking a button, the original message is **edited in place** to
     show the decision (e.g., `:white_check_mark: bash — Approved`) and the
     buttons are removed. This prevents double-clicks and reduces thread clutter.
   - Text replies ("yes"/"no") post a follow-up confirmation message instead.
8. To test approval timeout: trigger an approval and don't respond for 10 min.
   - The agent auto-denies the tool call.
   - Slack thread posts: `:hourglass: tool — approval timed out after 600s, denied automatically`.
   - The agent continues with a denial message in its conversation.

## If blocked
When blocked, do:
1) Set `runner/state.json.phase` to `BLOCKED`
2) Add `blockers` with exact missing info
3) Ask only 1-3 multiple-choice questions
