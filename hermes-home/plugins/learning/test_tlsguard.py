"""Certificate-check guard: terminal commands that switch TLS verification off are blocked.

Run: <hermes>/hermes-agent/venv/bin/python -m pytest -p no:cacheprovider plugins/learning/test_tlsguard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hermes-agent"))

from learning import learning as L  # noqa: E402


def pre(command):
    return L.on_pre_tool_call(tool_name="terminal", args={"command": command}, session_id="s1")


@pytest.mark.parametrize("command", [
    'curl -k "https://wheretostayamerica.com/"', 'curl -skL -A "Mozilla/5.0" https://x.org | head',
    "curl --insecure https://x.org", "wget --no-check-certificate https://x.org/a.pdf",
    "python3 -c \"import requests; requests.get('https://x.org', verify=False)\"",
    "PYTHONHTTPSVERIFY=0 python3 fetch.py", "NODE_TLS_REJECT_UNAUTHORIZED=0 node a.js",
    "python3 -c 'import ssl; ctx = ssl._create_unverified_context()'"])
def test_insecure_commands_are_blocked(command):
    r = pre(command)
    assert r and r["action"] == "block" and "certificate" in r["message"]


@pytest.mark.parametrize("command", [
    "curl -sL https://data.nola.gov/resource/en36-xvxg.json", "curl -X POST -H 'Content-Type: json' https://x.org",
    "wget -q https://x.org/a.pdf", "python3 -c \"import requests; requests.get('https://x.org', verify=True)\"",
    "ls -lk /workspace", "grep -k pattern file"])
def test_normal_commands_pass(command):
    assert pre(command) is None
