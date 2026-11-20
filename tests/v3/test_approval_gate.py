from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

from panopti.approval import HumanApprovalGate


def test_auto_approve():
    gate = HumanApprovalGate(auto_approve=True)
    assert gate.request_approval("tool", {"a": 1}) is True


def test_explicit_yes():
    gate = HumanApprovalGate()
    with patch("sys.stdin", StringIO("y\n")):
        with patch("select.select", return_value=([sys.stdin], [], [])):
            assert gate.request_approval("tool", {"a": 1}, timeout_seconds=1) is True


def test_explicit_no():
    gate = HumanApprovalGate()
    with patch("sys.stdin", StringIO("n\n")):
        with patch("select.select", return_value=([sys.stdin], [], [])):
            assert gate.request_approval("tool", {"a": 1}, timeout_seconds=1) is False


def test_timeout():
    gate = HumanApprovalGate()
    with patch("sys.stdin", StringIO("")):
        with patch("select.select", return_value=([], [], [])):
            assert gate.request_approval("tool", {"a": 1}, timeout_seconds=1) is False