"""Tests for poc.claude_client — retry logic and token tracking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from poc.claude_client import ClaudeClient, TokenUsage, _parse_retry_after


class TestTokenUsage:
    def test_add(self):
        usage = TokenUsage()
        mock_usage = MagicMock(input_tokens=100, output_tokens=50)
        usage.add(mock_usage)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_add_cumulative(self):
        usage = TokenUsage()
        usage.add(MagicMock(input_tokens=100, output_tokens=50))
        usage.add(MagicMock(input_tokens=200, output_tokens=100))
        assert usage.input_tokens == 300
        assert usage.output_tokens == 150


class TestClaudeClient:
    @patch("poc.claude_client.anthropic.Anthropic")
    def test_create_message_success(self, mock_anthropic):
        mock_instance = MagicMock()
        mock_anthropic.return_value = mock_instance

        mock_response = MagicMock()
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_instance.messages.create.return_value = mock_response

        client = ClaudeClient(model="claude-sonnet-4-5-20250929")
        result = client.create_message(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        )

        assert result == mock_response
        assert client.usage.input_tokens == 100
        assert client.usage.output_tokens == 50

    @patch("poc.claude_client.time.sleep")
    @patch("poc.claude_client.anthropic.Anthropic")
    def test_retries_on_server_error(self, mock_anthropic, mock_sleep):
        import anthropic

        mock_instance = MagicMock()
        mock_anthropic.return_value = mock_instance

        # First call fails, second succeeds
        error_response = MagicMock()
        error_response.status_code = 500

        mock_response = MagicMock()
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_instance.messages.create.side_effect = [
            anthropic.APIStatusError(
                message="Server error",
                response=error_response,
                body={"error": {"message": "Server error"}},
            ),
            mock_response,
        ]

        client = ClaudeClient(model="claude-sonnet-4-5-20250929")
        result = client.create_message(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        )

        assert result == mock_response
        assert mock_instance.messages.create.call_count == 2

    @patch("poc.claude_client.time.sleep")
    @patch("poc.claude_client.anthropic.Anthropic")
    def test_raises_on_client_error(self, mock_anthropic, mock_sleep):
        import anthropic

        mock_instance = MagicMock()
        mock_anthropic.return_value = mock_instance

        error_response = MagicMock()
        error_response.status_code = 400

        mock_instance.messages.create.side_effect = anthropic.APIStatusError(
            message="Bad request",
            response=error_response,
            body={"error": {"message": "Bad request"}},
        )

        client = ClaudeClient(model="claude-sonnet-4-5-20250929")
        with pytest.raises(anthropic.APIStatusError):
            client.create_message(
                messages=[{"role": "user", "content": "hello"}],
                tools=[],
            )

    @patch("poc.claude_client.time.sleep")
    @patch("poc.claude_client.anthropic.Anthropic")
    def test_retries_on_rate_limit(self, mock_anthropic, mock_sleep):
        import anthropic

        mock_instance = MagicMock()
        mock_anthropic.return_value = mock_instance

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {"retry-after": "1"}

        mock_response = MagicMock()
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_instance.messages.create.side_effect = [
            anthropic.RateLimitError(
                message="Rate limited",
                response=error_response,
                body={"error": {"message": "Rate limited"}},
            ),
            mock_response,
        ]

        client = ClaudeClient(model="claude-sonnet-4-5-20250929")
        result = client.create_message(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        )

        assert result == mock_response


class TestParseRetryAfter:
    def test_with_header(self):
        exc = MagicMock()
        exc.response.headers = {"retry-after": "3.5"}
        assert _parse_retry_after(exc) == 3.5

    def test_without_header(self):
        exc = MagicMock()
        exc.response.headers = {}
        assert _parse_retry_after(exc) == 5.0

    def test_no_response(self):
        exc = MagicMock(spec=[])
        assert _parse_retry_after(exc) == 5.0
