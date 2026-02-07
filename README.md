# claude-spec-bot

An autonomous Claude Code agent running inside Docker, controlled via Slack. Users submit tasks from Slack, see live progress, and approve/deny tool use (bash, file writes, git) via interactive messages.

## Components

**POC Runner** (`src/poc/`) — An autonomous agent running inside Docker. Calls the Claude API directly, executes tools (bash, file I/O, search, web search), and reports progress via HTTP callbacks on port 8000.

**Orchestrator** (`orchestrator_host/`) — Host-side Slack bot that connects to Slack via Socket Mode, receives `!poc` commands, dispatches jobs to the runner, and relays progress/approval interactions back to Slack threads.

## Quick Start

### Prerequisites

- Python 3.12+
- Docker Desktop with Compose v2
- Anthropic API key (required for agent mode)
- (Optional) Ollama running on the host

### Setup

```bash
# Create a local venv and install dev + orchestrator dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,orchestrator]"

# Copy and fill in runner secrets (ANTHROPIC_API_KEY required)
cp runner/.env.example runner/.env

# Build and start the runner container
docker compose build
docker compose up -d
```

### Verify the Runner

```bash
# Health check
curl -s http://localhost:8000/health
# {"status": "ok", "service": "poc-runner"}
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
| `!poc run [--model opus\|sonnet\|haiku] <task>` | Start the agent with a task |
| `!poc status [job_id]` | Show agent status (iteration, model, tokens) |
| `!poc cancel [job_id]` | Cancel a running or queued job |
| `!poc list` | List recent jobs |
| `!poc help` | Show help message |

Responses are posted in the Slack thread where the command was issued.

**Tool approval:** The agent will request approval for bash commands and file writes. You can approve/deny via Block Kit buttons or by replying in the thread with "yes"/"approve" or "no"/"deny". After you click a button, the original message is edited in place to show the decision (buttons are removed). If no response is given within 10 minutes, the tool call is automatically denied.

### Job State Machine

```
QUEUED -> RUNNING -> WAITING_APPROVAL -> RUNNING (resume) -> DONE
             |                                    |
          BLOCKED                             CANCELLED
             |
          CANCELLED

RUNNING -> FAILED
RUNNING -> CANCELLED
```

Per-job state is stored at `runner/jobs/<job_id>/state.json`.

## Project Structure

```
.
├── docker-compose.yml          # Runner service, port 8000, volume mounts
├── Dockerfile.poc              # Python 3.12-slim + git + ripgrep
├── pyproject.toml              # Project config, deps, entry points
├── src/poc/                    # Runner package (runs in Docker)
│   ├── __init__.py
│   ├── handler.py              # Multi-endpoint HTTP API (port 8000)
│   ├── agent.py                # AgentSession — core agent loop
│   ├── claude_client.py        # Anthropic SDK wrapper with retry
│   ├── tools.py                # Tool schemas and executors
│   └── callback.py             # HTTP event poster to orchestrator
├── orchestrator_host/          # Slack orchestrator (runs on host)
│   ├── __init__.py
│   ├── main.py                 # Entry point, wires all components
│   ├── slack_bot.py            # Slack Bolt app, !poc commands, Block Kit actions
│   ├── docker_exec.py          # HTTP client to runner
│   ├── state.py                # Job state dataclasses, file I/O
│   ├── jobs.py                 # Single-concurrency job queue
│   ├── callback_server.py      # HTTP server on :8001 for runner events
│   ├── progress.py             # Maps runner events to Slack messages
│   ├── approvals.py            # Tracks pending approvals, handles responses
│   └── .env.example            # Template for Slack tokens
├── tests/
│   ├── test_runner/            # Runner unit tests
│   │   ├── test_tools.py
│   │   ├── test_agent.py
│   │   ├── test_claude_client.py
│   │   └── test_handler.py
│   └── test_orchestrator/      # Orchestrator unit tests
│       ├── test_state.py
│       ├── test_docker_exec.py
│       ├── test_jobs.py
│       ├── test_slack_bot.py
│       ├── test_callback_server.py
│       ├── test_progress.py
│       └── test_approvals.py
└── runner/                     # Mounted state directory (Docker volume)
    ├── .env.example            # Template for runner secrets (ANTHROPIC_API_KEY)
    ├── state.json
    ├── plan.md
    ├── logs/
    ├── artifacts/
    └── jobs/                   # Per-job state (gitignored)
```

## End-to-End Testing

### Without Slack (runner only)

#### 1. Start the runner

```bash
# Ensure ANTHROPIC_API_KEY is set in runner/.env
docker compose up -d --build
```

#### 2. Health check

```bash
curl -s http://localhost:8000/health
# {"status": "ok", "service": "poc-runner"}
```

#### 3. Verify the Claude client can handle a job

Submit a simple read-only task that uses only auto-approved tools (`list_files`).
This confirms the API key is valid, the Claude client works, and the agent loop
completes end-to-end without requiring any approval interaction.

```bash
curl -s -X POST http://localhost:8000/jobs/smoke-test/start \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "List all Python files in the project and report their count",
    "callback_url": "",
    "model": "claude-sonnet-4-5-20250929"
  }'
# {"job_id": "smoke-test", "status": "started", "model": "claude-sonnet-4-5-20250929"}
```

Poll until the status field reads `completed`:

```bash
curl -s http://localhost:8000/jobs/smoke-test/status | python -m json.tool
# {
#   "job_id": "smoke-test",
#   "status": "completed",      <-- confirms the full loop worked
#   "iteration": 2,
#   "max_iterations": 200,
#   "model": "claude-sonnet-4-5-20250929",
#   "result_text": "Found 12 Python files..."
# }
```

If `status` is `failed`, check `result_text` for the error (usually an invalid
API key or network issue). You can also check the runner logs:

```bash
docker compose logs --tail 50
```

#### 4. Test the approval flow

Submit a task that requires a bash command (needs approval):

```bash
curl -s -X POST http://localhost:8000/jobs/test-approve/start \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Run python --version and report the output",
    "callback_url": ""
  }'
```

Poll status — it will transition to `waiting_approval` when bash is requested:

```bash
curl -s http://localhost:8000/jobs/test-approve/status | python -m json.tool
# {
#   "job_id": "test-approve",
#   "status": "waiting_approval",
#   "pending_approval": {
#     "tool_use_id": "toolu_abc123...",
#     "tool_name": "bash"
#   },
#   ...
# }
```

Approve the pending tool (copy the `tool_use_id` from above):

```bash
curl -s -X POST http://localhost:8000/jobs/test-approve/approve \
  -H "Content-Type: application/json" \
  -d '{"tool_use_id": "<tool_use_id from status>", "approved": true}'
```

Poll again — the job should finish with `status: "completed"`.

#### 5. Test approval timeout

Start a job that needs approval and **don't respond**. After 10 minutes (600s)
the tool call is automatically denied and the agent continues. For faster
testing, the timeout is configurable via the `approval_timeout` field in
`AgentSession` (e.g., set to 30 for a 30-second timeout).

### With Slack (full end-to-end)

1. Ensure `runner/.env` has `ANTHROPIC_API_KEY` set
2. Ensure `orchestrator_host/.env` has `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` set
3. Start the runner: `docker compose up -d --build`
4. Start the orchestrator: `source .venv/bin/activate && python -m orchestrator_host.main`
5. In Slack, send: `!poc run List all Python files in the project`
6. Watch the thread for progress messages and the final result
7. To test the approval flow: `!poc run Add a hello world test file`
   - The agent will request approval for `write_file` — click **Approve** or reply "yes" in the thread
   - After clicking, the button message is edited in place to show `:white_check_mark: write_file — Approved` (buttons are removed)
   - Click **Approve All** to auto-approve all future calls to that tool
   - Click **Deny** to reject — the agent receives a denial and continues without executing
8. To test approval timeout: trigger an approval and don't respond
   - After 10 minutes the tool is automatically denied
   - The thread posts `:hourglass: tool — approval timed out after 600s, denied automatically`
   - The agent continues with a denial message
