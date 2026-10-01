"""firecrawl-local web extract provider: loopback-only endpoint, policy checks, result shape.

Run: <hermes>/hermes-agent/venv/bin/python -m pytest -p no:cacheprovider plugins/firecrawl_local/test_provider.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hermes-agent"))

from firecrawl_local import provider as P  # noqa: E402


@pytest.mark.parametrize("url", ["http://127.0.0.1:3002", "http://localhost:3002/", "http://[::1]:3002"])
def test_loopback_urls_accepted(monkeypatch, url):
    monkeypatch.setenv("FIRECRAWL_LOCAL_URL", url)
    assert P.local_url().startswith("http://")
    assert P.FirecrawlLocalProvider().is_available()


@pytest.mark.parametrize("url", ["https://api.firecrawl.dev", "http://10.0.0.5:3002", "http://172.18.0.1:3002",
                                 "https://127.0.0.1:3002", "http://127.0.0.1.evil.example:3002", "http://127.0.0.1:3002/v2"])
def test_non_loopback_urls_refused(monkeypatch, url):
    monkeypatch.setenv("FIRECRAWL_LOCAL_URL", url)
    with pytest.raises(ValueError):
        P.local_url()
    assert not P.FirecrawlLocalProvider().is_available()
    out = asyncio.run(P.FirecrawlLocalProvider().extract(["https://example.com/"]))
    assert out[0]["error"]


def test_extract_only():
    p = P.FirecrawlLocalProvider()
    assert p.supports_extract() and not p.supports_search()


def _fake_post(payload, seen):
    async def post(self, url, json=None, **_):
        seen.append((url, json))
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
    return post


def test_extract_calls_local_api_and_shapes_result(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_LOCAL_URL", raising=False)
    monkeypatch.setattr(P, "check_website_access", lambda u: None)
    monkeypatch.setattr(P, "is_safe_url", lambda u: True)
    seen = []
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post({"success": True, "data": {
        "markdown": "# Hi", "html": "<h1>Hi</h1>", "metadata": {"title": "Hi", "url": "https://example.com/", "statusCode": 200}}}, seen))
    out = asyncio.run(P.FirecrawlLocalProvider().extract(["https://example.com/"]))
    assert seen[0][0] == "http://127.0.0.1:3002/v2/scrape" and seen[0][1]["url"] == "https://example.com/"
    assert out == [{"url": "https://example.com/", "title": "Hi", "content": "# Hi", "raw_content": "# Hi",
                    "metadata": {"title": "Hi", "description": None, "statusCode": 200, "url": "https://example.com/", "sourceURL": None}}]


def test_unsafe_target_and_unsafe_redirect_blocked(monkeypatch):
    monkeypatch.setattr(P, "check_website_access", lambda u: None)
    seen = []
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post({"success": True, "data": {
        "markdown": "secret", "metadata": {"url": "http://169.254.169.254/latest/meta-data/"}}}, seen))
    out = asyncio.run(P.FirecrawlLocalProvider().extract(["http://192.168.1.1/admin"]))
    assert "private" in out[0]["error"] and not seen  # never sent to the stack
    monkeypatch.setattr(P, "is_safe_url", lambda u: "169.254" not in u)
    out = asyncio.run(P.FirecrawlLocalProvider().extract(["https://example.com/r"]))
    assert "private" in out[0]["error"] and out[0]["content"] == ""


def test_stack_down_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(P, "check_website_access", lambda u: None)
    monkeypatch.setattr(P, "is_safe_url", lambda u: True)

    async def boom(self, url, **_):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    out = asyncio.run(P.FirecrawlLocalProvider().extract(["https://example.com/"]))
    assert "unreachable" in out[0]["error"]
