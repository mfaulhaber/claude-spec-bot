# Claude Instructions (Project Operating Guide)

## Project Overview

This project has two components:

1. **POC Runner** (`src/poc/`) — LLM provider abstraction with containerized build/test. Runs inside Docker.
2. **Orchestrator** (`orchestrator_host/`) — Slack bot that triggers and monitors the POC pipeline via `!poc` commands. Runs on the host.

## Execution Boundary
- Do NOT run shell commands on the host.
- Run all commands via Docker Compose service `runner`.
- Only mounted paths are:
  - `/workspace` = repo root
  - `/runner` = state/logs/artifacts (host-mounted `./runner`)
- Never request mounting `$HOME`, `~/.ssh`, `SSH_AUTH_SOCK`, `/var/run/docker.sock`, or any credential directories.
- **Exception**: The orchestrator (`orchestrator_host/`) runs on the host, not in Docker. It invokes Docker Compose via subprocess.

## Networking
- Internet access is allowed for API calls.
- Host Ollama is available at: `OLLAMA_BASE_URL=http://host.docker.internal:11434`
- If Ollama is unreachable, instruct the user to ensure Ollama is listening on an interface reachable by Docker (often via `OLLAMA_HOST=0.0.0.0:11434` for `ollama serve`).

## Secrets
- Secrets must not be committed.
- Use `runner/.env` for:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - optionally `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`
- Orchestrator tokens (host-side, not in `runner/.env`):
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
  - `docker-compose.yml`
  - `Dockerfile.poc`
- Runner directory:
  - `runner/state.json`
  - `runner/plan.md`
  - `runner/logs/`
  - `runner/artifacts/`
  - `runner/jobs/` (orchestrator job state, gitignored)
  - `runner/.env.example` (NOT `.env`)
- Scripts:
  - `scripts/bootstrap.sh`
  - `scripts/doctor.sh`
  - `scripts/lint.sh`
  - `scripts/test.sh`
  - `scripts/run.sh`
- Orchestrator (host-side):
  - `orchestrator_host/__init__.py`
  - `orchestrator_host/state.py`
  - `orchestrator_host/docker_exec.py`
  - `orchestrator_host/jobs.py`
  - `orchestrator_host/slack_bot.py`
  - `orchestrator_host/main.py`

## Package Layout
- `src/poc/` — POC package (installed inside Docker via `pip install -e ".[dev]"`)
- `orchestrator_host/` — Orchestrator package (installed on host via `pip install -e ".[orchestrator]"`)
- Both discovered by setuptools via `where = ["src", "."]` with `include = ["poc*", "orchestrator_host*"]`

## Build/Test Discipline
- Prefer `pyproject.toml` with a standard modern toolchain.
- Use `pytest` for tests.
- Use `ruff` for lint and format.
- Lint covers `src/`, `tests/`, and `orchestrator_host/`.
- Networked tests must be optional and skipped unless corresponding env vars are present.

## Definition of Done (DoD)
- `docker compose build` succeeds.
- `docker compose run --rm runner bash -lc "scripts/bootstrap.sh"` succeeds.
- `docker compose run --rm runner bash -lc "scripts/doctor.sh"` confirms:
  - Python version
  - Ollama reachable at `$OLLAMA_BASE_URL`
  - outbound HTTPS works
- `docker compose run --rm runner bash -lc "scripts/test.sh"` succeeds and writes logs + a short test report summary.

## How to run commands (examples)
- One-off:
  - `docker compose run --rm runner bash -lc "<cmd>"`
- Capture logs:
  - `docker compose run --rm runner bash -lc "<cmd> |& tee /runner/logs/<name>.log"`
- Start the Slack orchestrator (host-side):
  - `SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... python -m orchestrator_host.main`

## If blocked
When blocked, do:
1) Set `runner/state.json.phase` to `BLOCKED`
2) Add `blockers` with exact missing info
3) Ask only 1-3 multiple-choice questions
