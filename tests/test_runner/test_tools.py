"""Tests for poc.tools — tool executors and path validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from poc.tools import (
    TOOL_SCHEMAS,
    WEB_SEARCH_TOOL,
    _resolve_path,
    _truncate,
    execute_bash,
    execute_edit_file,
    execute_list_files,
    execute_read_file,
    execute_search_files,
    execute_tool,
    execute_write_file,
)


class TestResolvePathValidation:
    def test_absolute_within_workspace(self):
        with patch("poc.tools.WORKSPACE", Path("/workspace")):
            p = _resolve_path("/workspace/src/main.py")
            assert str(p).startswith("/workspace")

    def test_relative_resolved_to_workspace(self):
        with patch("poc.tools.WORKSPACE", Path("/workspace")):
            p = _resolve_path("src/main.py")
            assert str(p).startswith("/workspace")

    def test_runner_path_allowed(self):
        with patch("poc.tools.WORKSPACE", Path("/workspace")), \
             patch("poc.tools.RUNNER", Path("/runner")):
            p = _resolve_path("/runner/logs/test.log")
            assert str(p).startswith("/runner")

    def test_outside_workspace_rejected(self):
        with patch("poc.tools.WORKSPACE", Path("/workspace")), \
             patch("poc.tools.RUNNER", Path("/runner")):
            with pytest.raises(ValueError, match="outside"):
                _resolve_path("/etc/passwd")


class TestTruncate:
    def test_short_string(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_string(self):
        text = "a" * 100
        result = _truncate(text, 50)
        assert len(result) < 100
        assert "truncated" in result


class TestExecuteBash:
    @patch("poc.tools.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="hello world\n", stderr="", returncode=0
        )
        result = execute_bash("echo hello world")
        assert "hello world" in result
        mock_run.assert_called_once()

    @patch("poc.tools.subprocess.run")
    def test_failure_shows_exit_code(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", stderr="error msg", returncode=1
        )
        result = execute_bash("false")
        assert "exit code: 1" in result

    @patch("poc.tools.subprocess.run")
    def test_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=120)
        result = execute_bash("sleep 999", timeout=120)
        assert "timed out" in result

    @patch("poc.tools.subprocess.run")
    def test_timeout_clamped(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        execute_bash("echo test", timeout=9999)
        # Should be clamped to 600
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 600

    @patch("poc.tools.subprocess.run")
    def test_output_truncation(self, mock_run):
        big_output = "x" * 50000
        mock_run.return_value = MagicMock(stdout=big_output, stderr="", returncode=0)
        result = execute_bash("generate_output")
        assert len(result) < 50000
        assert "truncated" in result


class TestExecuteReadFile:
    def test_read_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_read_file(str(f))
        assert "1\tline1" in result
        assert "2\tline2" in result

    def test_read_file_with_offset(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_read_file(str(f), offset=2, limit=1)
        assert "2\tline2" in result
        assert "line1" not in result
        assert "line3" not in result

    def test_read_missing_file(self, tmp_path):
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_read_file(str(tmp_path / "nope.txt"))
        assert "not found" in result


class TestExecuteWriteFile:
    def test_write_file(self, tmp_path):
        target = tmp_path / "sub" / "output.txt"
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_write_file(str(target), "hello world")
        assert "Wrote" in result
        assert target.read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c.txt"
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            execute_write_file(str(target), "content")
        assert target.exists()


class TestExecuteEditFile:
    def test_edit_unique_match(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_edit_file(str(f), "hello", "goodbye")
        assert "Edited" in result
        assert f.read_text() == "goodbye world"

    def test_edit_not_found(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_edit_file(str(f), "xyz", "abc")
        assert "not found" in result

    def test_edit_non_unique(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("aaa bbb aaa")
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_edit_file(str(f), "aaa", "ccc")
        assert "2 times" in result


class TestExecuteListFiles:
    def test_list_files(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_list_files("*.py", str(tmp_path))
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_no_matches(self, tmp_path):
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_list_files("*.xyz", str(tmp_path))
        assert "No files" in result


class TestExecuteSearchFiles:
    @patch("poc.tools.subprocess.run")
    def test_search(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stdout="/workspace/test.py:1:hello world\n", returncode=0
        )
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_search_files("hello", str(tmp_path))
        assert "hello world" in result

    @patch("poc.tools.subprocess.run")
    def test_no_matches(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(stdout="", returncode=1)
        with patch("poc.tools.WORKSPACE", tmp_path), \
             patch("poc.tools.RUNNER", Path("/runner")):
            result = execute_search_files("xyz123", str(tmp_path))
        assert "no matches" in result


class TestExecuteTool:
    def test_unknown_tool(self):
        result = execute_tool("nonexistent", {})
        assert "unknown tool" in result

    @patch("poc.tools.subprocess.run")
    def test_dispatches_bash(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = execute_tool("bash", {"command": "echo ok"})
        assert "ok" in result

    def test_tool_schemas_well_formed(self):
        for schema in TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"


class TestWebSearchTool:
    def test_web_search_tool_schema(self):
        assert WEB_SEARCH_TOOL["type"] == "web_search_20250305"
        assert WEB_SEARCH_TOOL["name"] == "web_search"
        # Server tools don't have input_schema
        assert "input_schema" not in WEB_SEARCH_TOOL
