# POC Implementation Plan

## Assumptions
- Running on macOS with Docker Desktop installed
- Ollama may or may not be running on the host
- API keys for OpenAI/Anthropic are optional; tests skip gracefully without them
- Python 3.12 in the container (host Python is irrelevant)

## Decisions
- **Package layout**: `src/poc/` with editable install via `pyproject.toml`
- **LLM abstraction**: Three provider classes (`OllamaProvider`, `OpenAIProvider`, `AnthropicProvider`) behind a common interface
- **CLI**: Click-based CLI with `poc llm` subcommand
- **Lint**: `ruff` for both linting and formatting
- **Test**: `pytest` with network tests marked and skipped unless env vars present
- **Venv**: Persistent venv at `/runner/venv` (survives container restarts via mounted volume)

## Task Checklist
- [x] Create CLAUDE.md
- [x] Create Dockerfile.poc + docker-compose.yml
- [x] Create .gitignore
- [x] Create runner/ directory structure (state.json, plan.md, logs/, artifacts/, .env.example)
- [x] Create scripts/ (bootstrap.sh, doctor.sh, lint.sh, test.sh, run.sh)
- [x] Create pyproject.toml
- [x] Create src/poc/ package (providers, CLI)
- [x] Create tests/
- [x] Build container — `docker compose build` succeeds
- [x] Run bootstrap — deps install into /runner/venv
- [x] Run doctor — 7/7 checks pass (Python, deps, Ollama, HTTPS)
- [x] Run lint — all checks pass
- [x] Run test — 11 passed, 3 skipped
- [x] Confirm DoD and set phase=DONE

## Slack Orchestrator (Phase 2)
- [x] Create `orchestrator_host/state.py` — job state dataclasses, ID generation, file I/O with fcntl locking
- [x] Create `orchestrator_host/docker_exec.py` — subprocess wrapper for docker compose run
- [x] Create `orchestrator_host/jobs.py` — JobQueue, pipeline execution, crash recovery
- [x] Create `orchestrator_host/slack_bot.py` — Slack Bolt app, command parsing, SlackCallback
- [x] Create `orchestrator_host/main.py` — entry point with prerequisite checks
- [x] Create `tests/test_orchestrator/` — 4 test modules (state, docker_exec, jobs, slack_bot)
- [x] Update `pyproject.toml` — orchestrator optional deps, entry point, package discovery
- [x] Update `.gitignore` — add `runner/jobs/`
- [x] Update `scripts/lint.sh` — include `orchestrator_host/` in lint paths
- [x] Lint passes — all checks pass
- [x] Tests pass — 63 passed, 3 skipped

## Commands Used
```bash
# Build
docker compose build

# Bootstrap deps
docker compose run --rm runner bash -lc "scripts/bootstrap.sh"

# Doctor check
docker compose run --rm runner bash -lc "scripts/doctor.sh"

# Lint
docker compose run --rm runner bash -lc "scripts/lint.sh"

# Test
docker compose run --rm runner bash -lc "scripts/test.sh"

# Run CLI
docker compose run --rm runner bash -lc "scripts/run.sh llm --backend ollama --model llama3.1 --prompt 'hello'"

# Run orchestrator (host-side, requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN)
python -m orchestrator_host.main
```

## Definition of Done
1. [x] `docker compose build` succeeds
2. [x] `scripts/bootstrap.sh` installs all deps
3. [x] `scripts/doctor.sh` confirms Python version, Ollama reachability, outbound HTTPS
4. [x] `scripts/test.sh` passes and writes test report
5. [x] `runner/state.json` phase = DONE
