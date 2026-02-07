# claude-spec-bot

A containerized POC runner with LLM provider abstraction and a Slack-controlled orchestrator.

## Components

**POC Runner** (`src/poc/`) — A Python HTTP message handler running inside Docker. Receives messages from the orchestrator, processes them, and returns responses on port 8000.

**Orchestrator** (`orchestrator_host/`) — Host-side Slack bot that connects to Slack via Socket Mode, receives `!poc` commands from users, and forwards messages to the runner via HTTP POST.

## Quick Start

### Prerequisites

- Python 3.12+
- Docker Desktop with Compose v2
- (Optional) Ollama running on the host
- (Optional) OpenAI / Anthropic API keys

### Setup

```bash
# Create a local venv and install dev + orchestrator dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,orchestrator]"

# Copy and fill in runner secrets (optional, for LLM API keys)
cp runner/.env.example runner/.env

# Build and start the runner container
docker compose build
docker compose up -d
```

### Verify the Runner

```bash
# Health check
curl -s http://localhost:8000
# {"status": "ok", "service": "poc-runner"}

# Send a test message
curl -s -X POST http://localhost:8000 \
  -H "Content-Type: application/json" \
  -d '{"job_id": "test-123", "message": "hello world"}'
# {"job_id": "test-123", "status": "processed", "reply": "..."}
```

### Run Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

### Run Linter

```bash
source .venv/bin/activate
ruff check src/ tests/ orchestrator_host/
ruff format --check src/ tests/ orchestrator_host/
```

## Slack Orchestrator

The orchestrator runs on your host machine and connects to Slack via Socket Mode (no public URL needed).

### 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** > **From scratch**.
2. Name it (e.g. `POC Bot`), select your workspace, and click **Create App**.

### 2. Generate an App-Level Token (`SLACK_APP_TOKEN`)

1. In the left sidebar: **Settings > Basic Information**.
2. Scroll to **App-Level Tokens** and click **Generate Token and Scopes**.
3. Name it (e.g. `socket-token`), add the scope `connections:write`, and click **Generate**.
4. Copy the `xapp-...` value.

### 3. Enable Socket Mode

1. Left sidebar: **Settings > Socket Mode**.
2. Toggle **Enable Socket Mode** to **On**.

### 4. Subscribe to Events

1. Left sidebar: **Features > Event Subscriptions**.
2. Toggle **Enable Events** to **On**.
3. Under **Subscribe to bot events**, add:
   - `message.channels` (public channels)
   - `message.groups` (private channels, optional)
4. Click **Save Changes**.

### 5. Add Bot Permissions

1. Left sidebar: **Features > OAuth & Permissions**.
2. Under **Bot Token Scopes**, add:
   - `chat:write`
   - `channels:history` (or `groups:history` for private channels)
   - `app_mentions:read`

### 6. Install the App and Get the Bot Token (`SLACK_BOT_TOKEN`)

1. Left sidebar: **Settings > Install App**.
2. Click **Install to Workspace** and authorize.
3. Copy the **Bot User OAuth Token** (`xoxb-...`).

### 7. Configure Tokens

Copy the example env file and fill in both tokens:

```bash
cp orchestrator_host/.env.example orchestrator_host/.env
```

```
# orchestrator_host/.env
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
```

### 8. Create a Slack Channel

1. In Slack, create a channel for the bot (e.g. `#poc-bot`).
2. Invite the bot to the channel: `/invite @POC Bot`.

### 9. Install and Run

```bash
# Create a local venv and install
python -m venv .venv
source .venv/bin/activate
pip install -e ".[orchestrator]"

# Start the orchestrator
python -m orchestrator_host.main
```

Verify by typing `!poc help` in the channel — the bot should respond.

### Slack Commands

| Command | Description |
|---|---|
| `!poc run <message>` | Send a message to the runner and get a response |
| `!poc status [job_id]` | Show status of a job (defaults to current) |
| `!poc cancel [job_id]` | Cancel a running or queued job |
| `!poc list` | List recent jobs |
| `!poc help` | Show help message |

Responses are posted in the Slack thread where the command was issued.

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
├── docker-compose.yml          # Runner service, port 8000, volume mounts
├── Dockerfile.poc              # Python 3.12-slim, starts poc.handler
├── pyproject.toml              # Project config, deps, entry points
├── src/poc/                    # Runner package (runs in Docker)
│   ├── __init__.py
│   └── handler.py              # HTTP message handler (port 8000)
├── orchestrator_host/          # Slack orchestrator (runs on host)
│   ├── __init__.py
│   ├── main.py                 # Entry point, loads .env, starts Socket Mode
│   ├── slack_bot.py            # Slack Bolt app, !poc command handlers
│   ├── docker_exec.py          # HTTP client to runner + Docker helpers
│   ├── state.py                # Job state dataclasses, file I/O
│   ├── jobs.py                 # Job queue, pipeline execution
│   └── .env.example            # Template for Slack tokens
├── tests/
│   └── test_orchestrator/      # Orchestrator unit tests
│       ├── test_state.py
│       ├── test_docker_exec.py
│       ├── test_jobs.py
│       └── test_slack_bot.py
└── runner/                     # Mounted state directory (Docker volume)
    ├── .env.example            # Template for runner secrets
    ├── state.json
    ├── plan.md
    ├── logs/
    ├── artifacts/
    └── jobs/                   # Per-job state (gitignored)
```
