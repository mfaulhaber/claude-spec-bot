"""Hello World test file."""

from __future__ import annotations


def hello_world() -> str:
    """Return a hello world message."""
    return "Hello, World!"


class TestHelloWorld:
    """Test class for hello world functionality."""

    def test_hello_world(self):
        """Test that hello_world returns the correct message."""
        result = hello_world()
        assert result == "Hello, World!"
        assert isinstance(result, str)

    def test_hello_world_not_empty(self):
        """Test that hello_world returns a non-empty string."""
        result = hello_world()
        assert len(result) > 0

    def test_hello_world_contains_hello(self):
        """Test that hello_world contains 'Hello'."""
        result = hello_world()
        assert "Hello" in result
