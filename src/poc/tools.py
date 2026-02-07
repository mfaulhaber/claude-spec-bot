"""Tool definitions and executors for the Claude agent.

Each tool has a schema (for the Claude API) and an executor function that
returns a string result.  All file paths are resolved relative to /workspace
and validated to stay within /workspace or /runner.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

WORKSPACE = Path("/workspace")
RUNNER = Path("/runner")
MAX_OUTPUT_CHARS = 30_000
DEFAULT_BASH_TIMEOUT = 120
MAX_BASH_TIMEOUT = 600

# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _resolve_path(raw: str) -> Path:
    """Resolve a path relative to /workspace, ensuring it stays within allowed roots."""
    p = Path(raw)
    if not p.is_absolute():
        p = WORKSPACE / p
    resolved = p.resolve()
    if not (
        str(resolved).startswith(str(WORKSPACE.resolve()))
        or str(resolved).startswith(str(RUNNER.resolve()))
    ):
        raise ValueError(
            f"Path {raw!r} resolves to {resolved} which is outside "
            f"/workspace and /runner"
        )
    return resolved


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return (
        text[:half]
        + f"\n\n... ({len(text) - limit} characters truncated) ...\n\n"
        + text[-half:]
    )


# ---------------------------------------------------------------------------
# Tool schemas (Claude API format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "bash",
        "description": (
            "Execute a bash command in /workspace. "
            "Use for running tests, installing packages, git operations, etc. "
            "Commands run with a configurable timeout (default 120s, max 600s). "
            "Output is captured and truncated to 30,000 characters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120, max 600).",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file from the workspace. Returns the file content with line numbers. "
            "Optionally specify offset (1-based line number) and limit (number of lines)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to /workspace or absolute).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Starting line number (1-based). Default: 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read. Default: all.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file, creating parent directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to /workspace or absolute).",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Edit a file by replacing an exact string match. "
            "The old_string must appear exactly once in the file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to /workspace or absolute).",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact string to find (must be unique in the file).",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement string.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List files matching a glob pattern in the workspace. "
            "Returns matching file paths, one per line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts').",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: /workspace).",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search file contents using grep. Returns matching lines with file paths "
            "and line numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex).",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: /workspace).",
                },
                "include": {
                    "type": "string",
                    "description": "File glob to include (e.g. '*.py').",
                },
            },
            "required": ["pattern"],
        },
    },
]

# Server-side tool (Anthropic handles execution; no client-side executor needed)
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


def execute_bash(command: str, timeout: int | None = None) -> str:
    """Execute a bash command in /workspace."""
    if timeout is None:
        timeout = DEFAULT_BASH_TIMEOUT
    timeout = min(max(1, timeout), MAX_BASH_TIMEOUT)

    log.info("bash: %s (timeout=%ds)", command, timeout)
    try:
        result = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return _truncate(output) if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"[Command timed out after {timeout}s]"


def execute_read_file(path: str, offset: int = 1, limit: int | None = None) -> str:
    """Read a file with optional offset and limit, returning numbered lines."""
    resolved = _resolve_path(path)
    if not resolved.exists():
        return f"Error: file not found: {path}"
    if not resolved.is_file():
        return f"Error: not a file: {path}"

    lines = resolved.read_text().splitlines()
    start = max(0, offset - 1)
    end = start + limit if limit else len(lines)
    selected = lines[start:end]

    numbered = [f"{start + i + 1}\t{line}" for i, line in enumerate(selected)]
    return _truncate("\n".join(numbered))


def execute_write_file(path: str, content: str) -> str:
    """Write content to a file, creating parent dirs."""
    resolved = _resolve_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content)
    return f"Wrote {len(content)} bytes to {path}"


def execute_edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace an exact unique string in a file."""
    resolved = _resolve_path(path)
    if not resolved.exists():
        return f"Error: file not found: {path}"

    content = resolved.read_text()
    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {path}"
    if count > 1:
        return f"Error: old_string appears {count} times in {path} (must be unique)"

    new_content = content.replace(old_string, new_string, 1)
    resolved.write_text(new_content)
    return f"Edited {path} (replaced 1 occurrence)"


def execute_list_files(pattern: str, path: str | None = None) -> str:
    """List files matching a glob pattern."""
    base = _resolve_path(path) if path else WORKSPACE
    if not base.is_dir():
        return f"Error: not a directory: {path}"

    matches = sorted(str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file())
    if not matches:
        return f"No files matching '{pattern}' in {base}"
    return _truncate("\n".join(matches))


def execute_search_files(
    pattern: str, path: str | None = None, include: str | None = None
) -> str:
    """Search file contents using grep."""
    base = _resolve_path(path) if path else WORKSPACE
    cmd = ["grep", "-rn", "--color=never"]
    if include:
        cmd.extend(["--include", include])
    cmd.extend([pattern, str(base)])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        output = result.stdout or "(no matches)"
        return _truncate(output)
    except subprocess.TimeoutExpired:
        return "[Search timed out after 30s]"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EXECUTORS = {
    "bash": lambda inp: execute_bash(inp["command"], inp.get("timeout")),
    "read_file": lambda inp: execute_read_file(
        inp["path"], inp.get("offset", 1), inp.get("limit")
    ),
    "write_file": lambda inp: execute_write_file(inp["path"], inp["content"]),
    "edit_file": lambda inp: execute_edit_file(
        inp["path"], inp["old_string"], inp["new_string"]
    ),
    "list_files": lambda inp: execute_list_files(inp["pattern"], inp.get("path")),
    "search_files": lambda inp: execute_search_files(
        inp["pattern"], inp.get("path"), inp.get("include")
    ),
}


def execute_tool(name: str, tool_input: dict) -> str:
    """Dispatch a tool call to the appropriate executor. Returns the result string."""
    executor = _EXECUTORS.get(name)
    if executor is None:
        return f"Error: unknown tool '{name}'"
    try:
        return executor(tool_input)
    except Exception as exc:
        log.exception("Tool %s failed", name)
        return f"Error executing {name}: {exc}"
