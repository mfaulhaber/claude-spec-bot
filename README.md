# claude-spec-bot

A containerized POC runner with LLM provider abstraction and a Slack-controlled orchestrator.

## Components

**POC Runner** (`src/poc/`) — CLI tool for sending prompts to LLM providers (Ollama, OpenAI, Anthropic) with a unified interface. Runs inside Docker.

**Orchestrator** (`orchestrator_host/`) — Host-side Slack bot that lets users trigger and monitor the build/test pipeline from a Slack channel using `!poc` commands.

## Quick Start

### Prerequisites

- Docker Desktop with Compose v2
- (Optional) Ollama running on the host
- (Optional) OpenAI / Anthropic API keys

### Setup

```bash
# Copy and fill in secrets
cp runner/.env.example runner/.env

# Build the container
docker compose build

# Install dependencies
docker compose run --rm runner bash -lc "scripts/bootstrap.sh"

# Verify environment
docker compose run --rm runner bash -lc "scripts/doctor.sh"
```

### Run Tests

```bash
docker compose run --rm runner bash -lc "scripts/test.sh"
```

### Run Linter

```bash
docker compose run --rm runner bash -lc "scripts/lint.sh"
```

### Use the CLI

```bash
# Ollama (requires Ollama running on host)
docker compose run --rm runner bash -lc "scripts/run.sh llm --backend ollama --model llama3.2 --prompt 'Hello'"

# OpenAI (requires OPENAI_API_KEY in runner/.env)
docker compose run --rm runner bash -lc "scripts/run.sh llm --backend openai --model gpt-4o-mini --prompt 'Hello'"

# Anthropic (requires ANTHROPIC_API_KEY in runner/.env)
docker compose run --rm runner bash -lc "scripts/run.sh llm --backend anthropic --model claude-haiku-4-5-20251001 --prompt 'Hello'"
```

## Slack Orchestrator

The orchestrator runs on your host machine and connects to Slack via Socket Mode.

### Setup

1. Create a Slack app with Socket Mode enabled and the following scopes:
   - `chat:write`
   - `channels:history` (or `groups:history` for private channels)
   - `app_mentions:read`

2. Install the orchestrator dependencies:
   ```bash
   pip install -e ".[orchestrator]"
   ```

3. Export your Slack tokens:
   ```bash
   export SLACK_BOT_TOKEN=xoxb-...
   export SLACK_APP_TOKEN=xapp-...
   ```

4. Start the orchestrator:
   ```bash
   python -m orchestrator_host.main
   ```

### Slack Commands

| Command | Description |
|---|---|
| `!poc run <goal>` | Start a new pipeline run (bootstrap, doctor, lint, test) |
| `!poc status [job_id]` | Show status of a job (defaults to current) |
| `!poc cancel [job_id]` | Cancel a running or queued job |
| `!poc list` | List recent jobs |
| `!poc help` | Show help message |

Pipeline progress is reported in the Slack thread where the command was issued.

### Job State Machine

```
QUEUED -> RUNNING -> DONE
             |         ^
          BLOCKED -> RUNNING (resume)
             |
          CANCELLED

RUNNING -> FAILED
RUNNING -> CANCELLED
```

Per-job state is stored at `runner/jobs/<job_id>/state.json` with logs at `runner/jobs/<job_id>/logs/`.

## Project Structure

```
.
├── docker-compose.yml          # Docker services (runner, runner_offline)
├── Dockerfile.poc              # Python 3.12 container image
├── pyproject.toml              # Project config, deps, entry points
├── src/poc/                    # POC package (runs in Docker)
│   ├── __init__.py
│   ├── cli.py                  # Click CLI
│   └── providers.py            # LLM provider abstraction
├── orchestrator_host/          # Slack orchestrator (runs on host)
│   ├── __init__.py
│   ├── state.py                # Job state dataclasses, file I/O
│   ├── docker_exec.py          # Docker Compose subprocess wrapper
│   ├── jobs.py                 # Job queue, pipeline execution
│   ├── slack_bot.py            # Slack Bolt app, command handlers
│   └── main.py                 # Entry point
├── tests/
│   ├── test_providers.py       # Provider unit + integration tests
│   └── test_orchestrator/      # Orchestrator unit tests
│       ├── test_state.py
│       ├── test_docker_exec.py
│       ├── test_jobs.py
│       └── test_slack_bot.py
├── scripts/
│   ├── bootstrap.sh            # Install deps into persistent venv
│   ├── doctor.sh               # Connectivity and environment checks
│   ├── lint.sh                 # ruff check + format
│   ├── test.sh                 # pytest with report generation
│   └── run.sh                  # CLI wrapper
└── runner/                     # Mounted state directory
    ├── state.json              # Global project state
    ├── plan.md                 # Implementation plan and checklist
    ├── .env.example            # Template for secrets
    ├── logs/                   # Build/test logs
    ├── artifacts/              # Reports and outputs
    └── jobs/                   # Per-job state (gitignored)
```
