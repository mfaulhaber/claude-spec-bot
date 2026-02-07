"""Tests for orchestrator_host.approvals."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from orchestrator_host.approvals import ApprovalManager


class TestApprovalManager:
    def test_register_and_get_pending(self):
        mgr = ApprovalManager()
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")
        pending = mgr.get_pending("job-1")
        assert pending is not None
        assert pending["tool_use_id"] == "tu-1"
        assert pending["tool_name"] == "bash"

    def test_get_pending_none(self):
        mgr = ApprovalManager()
        assert mgr.get_pending("job-1") is None

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_approve(self, mock_send):
        mgr = ApprovalManager(slack_client=MagicMock())
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        result = mgr.handle_approve("job-1", "tu-1")
        assert result is True
        mock_send.assert_called_once_with("job-1", "tu-1", approved=True, auto_approve_tool=False)
        assert mgr.get_pending("job-1") is None

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_approve_all(self, mock_send):
        mgr = ApprovalManager(slack_client=MagicMock())
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        result = mgr.handle_approve("job-1", "tu-1", auto_all=True)
        assert result is True
        mock_send.assert_called_once_with("job-1", "tu-1", approved=True, auto_approve_tool=True)

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_deny(self, mock_send):
        mgr = ApprovalManager(slack_client=MagicMock())
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        result = mgr.handle_deny("job-1", "tu-1")
        assert result is True
        mock_send.assert_called_once_with("job-1", "tu-1", approved=False)

    def test_handle_approve_wrong_tool_id(self):
        mgr = ApprovalManager()
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        result = mgr.handle_approve("job-1", "tu-wrong")
        assert result is False

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_text_reply_approve(self, mock_send):
        mgr = ApprovalManager()
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        assert mgr.handle_text_reply("job-1", "yes") is True
        mock_send.assert_called_once()

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_text_reply_deny(self, mock_send):
        mgr = ApprovalManager()
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        assert mgr.handle_text_reply("job-1", "no") is True
        mock_send.assert_called_once_with("job-1", "tu-1", approved=False)

    def test_handle_text_reply_unknown_text(self):
        mgr = ApprovalManager()
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        assert mgr.handle_text_reply("job-1", "maybe") is False

    def test_handle_text_reply_no_pending(self):
        mgr = ApprovalManager()
        assert mgr.handle_text_reply("job-1", "yes") is False

    def test_clear_job(self):
        mgr = ApprovalManager()
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")
        mgr.clear_job("job-1")
        assert mgr.get_pending("job-1") is None

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_approve_with_message_ts_uses_chat_update(self, mock_send):
        client = MagicMock()
        mgr = ApprovalManager(slack_client=client)
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        mgr.handle_approve("job-1", "tu-1", message_ts="msg-ts-1")

        client.chat_update.assert_called_once()
        call_kwargs = client.chat_update.call_args[1]
        assert call_kwargs["ts"] == "msg-ts-1"
        assert call_kwargs["channel"] == "C123"
        assert "Approved" in call_kwargs["text"]
        assert call_kwargs["blocks"] == []
        client.chat_postMessage.assert_not_called()

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_deny_with_message_ts_uses_chat_update(self, mock_send):
        client = MagicMock()
        mgr = ApprovalManager(slack_client=client)
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        mgr.handle_deny("job-1", "tu-1", message_ts="msg-ts-1")

        client.chat_update.assert_called_once()
        call_kwargs = client.chat_update.call_args[1]
        assert call_kwargs["ts"] == "msg-ts-1"
        assert "Denied" in call_kwargs["text"]
        client.chat_postMessage.assert_not_called()

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_approve_without_message_ts_falls_back_to_post(self, mock_send):
        client = MagicMock()
        mgr = ApprovalManager(slack_client=client)
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        mgr.handle_approve("job-1", "tu-1")

        client.chat_postMessage.assert_called_once()
        client.chat_update.assert_not_called()

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_text_approve_variants(self, mock_send):
        for word in ["yes", "y", "approve", "ok", "go"]:
            mgr = ApprovalManager()
            mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")
            assert mgr.handle_text_reply("job-1", word) is True

    @patch("orchestrator_host.approvals.send_approval")
    def test_handle_text_deny_variants(self, mock_send):
        for word in ["no", "n", "deny", "reject", "stop"]:
            mgr = ApprovalManager()
            mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")
            assert mgr.handle_text_reply("job-1", word) is True
