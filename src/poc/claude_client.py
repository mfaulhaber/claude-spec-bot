"""Thin wrapper around the Anthropic SDK with retry logic."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import anthropic

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_RETRIES = 3
MAX_TOKENS = 8192

SYSTEM_PROMPT = """\
You are an autonomous coding agent running inside a Docker container.
Your workspace is mounted at /workspace and you have full access to the codebase.

You can use the following tools:
- bash: Run shell commands (tests, git, package management, etc.)
- read_file: Read file contents with line numbers
- write_file: Create or overwrite files
- edit_file: Make targeted edits (find and replace exact strings)
- list_files: Find files matching glob patterns
- search_files: Search file contents with grep

Work methodically:
1. Understand the task by reading relevant files
2. Plan your approach
3. Implement changes
4. Verify your work (run tests, check output)
5. Report what you did

Be concise in your responses. Focus on completing the task efficiently.
"""


@dataclass
class TokenUsage:
    """Cumulative token usage tracker."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, usage) -> None:
        """Add usage from an API response."""
        self.input_tokens += getattr(usage, "input_tokens", 0)
        self.output_tokens += getattr(usage, "output_tokens", 0)
        self.cache_creation_input_tokens += getattr(
            usage, "cache_creation_input_tokens", 0
        )
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0)


@dataclass
class ClaudeClient:
    """Wrapper around the Anthropic messages API with retry."""

    model: str = DEFAULT_MODEL
    max_tokens: int = MAX_TOKENS
    usage: TokenUsage = field(default_factory=TokenUsage)
    _client: anthropic.Anthropic | None = field(default=None, repr=False)

    def __post_init__(self):
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
            self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    def create_message(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str = SYSTEM_PROMPT,
        model: str | None = None,
    ) -> anthropic.types.Message:
        """Call the Claude messages API with retries on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._client.messages.create(
                    model=model or self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=messages,
                    tools=tools,
                )
                self.usage.add(response.usage)
                return response
            except anthropic.RateLimitError as exc:
                last_exc = exc
                retry_after = _parse_retry_after(exc)
                log.warning(
                    "Rate limited (attempt %d/%d), waiting %.1fs",
                    attempt, MAX_RETRIES, retry_after,
                )
                time.sleep(retry_after)
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_exc = exc
                    wait = 2 ** attempt
                    log.warning(
                        "Server error %d (attempt %d/%d), waiting %ds",
                        exc.status_code, attempt, MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                else:
                    raise
            except anthropic.APIConnectionError as exc:
                last_exc = exc
                wait = 2 ** attempt
                log.warning(
                    "Connection error (attempt %d/%d), waiting %ds",
                    attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
        raise last_exc  # type: ignore[misc]


def _parse_retry_after(exc: anthropic.RateLimitError) -> float:
    """Extract retry-after from rate limit response headers."""
    try:
        headers = getattr(exc, "response", None)
        if headers is not None:
            val = headers.headers.get("retry-after")
            if val:
                return float(val)
    except (AttributeError, ValueError):
        pass
    return 5.0
