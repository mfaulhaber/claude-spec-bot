# Claude Instructions (Project Operating Guide)

## Project Overview

This project has two components that communicate over HTTP:

1. **POC Runner** (`src/poc/`) — A Python HTTP message handler running inside Docker. Receives messages from the orchestrator, processes them, and returns responses. Starts automatically with the container on port 8000.
2. **Orchestrator** (`orchestrator_host/`) — A host-side Slack bot that connects to Slack via Socket Mode. Receives `!poc` commands from users and forwards messages to the runner via HTTP POST.

## Architecture

```
Slack (Socket Mode)
    │
    ▼
┌──────────────────────────┐
│  Orchestrator (host)     │
│  orchestrator_host/      │
│  - Slack Bolt app        │
│  - Parses !poc commands  │
│  - Manages job state     │
│  - Loads tokens from     │
│    orchestrator_host/.env│
└──────────┬───────────────┘
           │ HTTP POST (localhost:8000)
           ▼
┌──────────────────────────┐
│  Runner (Docker)         │
│  src/poc/handler.py      │
│  - HTTP server on :8000  │
│  - Receives JSON messages│
│  - Returns JSON responses│
│  - Mounts:               │
│    /workspace = repo root│
│    /runner = state/logs   │
└──────────────────────────┘
```

**Message flow**: Slack user sends `!poc run <message>` → Orchestrator receives via Socket Mode → POSTs `{"job_id": "...", "message": "..."}` to `http://localhost:8000` → Runner processes and returns `{"job_id": "...", "status": "processed", "reply": "..."}` → Orchestrator posts reply back to Slack thread.

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
- The orchestrator communicates with the runner via HTTP at `http://localhost:8000`.
- Internet access is allowed for API calls.
- Host Ollama is available at: `OLLAMA_BASE_URL=http://host.docker.internal:11434`
- If Ollama is unreachable, instruct the user to ensure Ollama is listening on an interface reachable by Docker (often via `OLLAMA_HOST=0.0.0.0:11434` for `ollama serve`).

## Secrets
- Secrets must not be committed.
- Use `runner/.env` for:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - optionally `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`
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

## Required Project Layout
- Docker:
  - `docker-compose.yml` — Defines the `runner` service, port mapping (8000:8000), volume mounts, env
  - `Dockerfile.poc` — Python 3.12-slim image, starts `python -m poc.handler`
- Runner (Docker-side):
  - `src/poc/__init__.py`
  - `src/poc/handler.py` — HTTP message handler (port 8000)
  - `runner/.env.example` (NOT `.env`)
  - `runner/state.json`, `runner/plan.md`
  - `runner/logs/`, `runner/artifacts/`
  - `runner/jobs/` (orchestrator job state, gitignored)
- Orchestrator (host-side):
  - `orchestrator_host/__init__.py`
  - `orchestrator_host/main.py` — Entry point, loads .env, starts Socket Mode
  - `orchestrator_host/slack_bot.py` — Slack Bolt app, `!poc` command handlers
  - `orchestrator_host/docker_exec.py` — HTTP client to runner + Docker subprocess helpers
  - `orchestrator_host/state.py` — Job state dataclasses, file I/O with locking
  - `orchestrator_host/jobs.py` — Job queue, pipeline execution
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
- All 52+ orchestrator tests must pass.
- Networked tests are skipped automatically when API keys are absent.
- **If any runner code changed** (`src/poc/`, `Dockerfile.poc`, `docker-compose.yml`):
  - Rebuild and restart the container: `docker compose up -d --build`
  - Verify the runner responds: `curl -s http://localhost:8000`

## Definition of Done (DoD)
- Unit tests pass: `python -m pytest tests/ -v`
- `docker compose build` succeeds.
- `docker compose up -d` starts the runner and it responds on port 8000.
- `curl -s http://localhost:8000` returns `{"status": "ok", "service": "poc-runner"}`.

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
- Test the runner directly:
  - `curl -X POST http://localhost:8000 -H "Content-Type: application/json" -d '{"job_id":"test","message":"hello"}'`

## If blocked
When blocked, do:
1) Set `runner/state.json.phase` to `BLOCKED`
2) Add `blockers` with exact missing info
3) Ask only 1-3 multiple-choice questions
