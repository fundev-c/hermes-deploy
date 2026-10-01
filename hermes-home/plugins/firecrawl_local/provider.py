"""web_extract via the sandboxed self-hosted Firecrawl (hermes-deploy/firecrawl).

Extract only: search goes to Firecrawl cloud through the bundled ``firecrawl`` provider, which
reads FIRECRAWL_API_KEY. This provider reads FIRECRAWL_LOCAL_URL (default http://127.0.0.1:3002)
and refuses anything that is not loopback, so a config or env slip can never point extraction at
a remote service. The same website-policy and SSRF checks as the bundled provider run before the
request and again on the post-redirect URL; the stack's own egress proxy (fc-egress) is the
second layer.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from typing import Any, Dict, List
from urllib.parse import urlsplit

import httpx

from plugins.web._common import BaseWebSearchProvider, setup_schema
from tools.url_safety import is_safe_url
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:3002"
SCRAPE_TIMEOUT = 60


def local_url() -> str:
    """The configured endpoint, or ValueError if it is not plain-http loopback."""
    url = (os.environ.get("FIRECRAWL_LOCAL_URL") or DEFAULT_URL).strip().rstrip("/")
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme != "http" or not loopback or parts.path not in ("", "/"):
        raise ValueError(f"FIRECRAWL_LOCAL_URL must be http://127.0.0.1:<port> (got {url!r})")
    return url


def _err(url: str, error: str, **extra: Any) -> Dict[str, Any]:
    return {"url": url, "title": "", "content": "", "error": error, **extra}


class FirecrawlLocalProvider(BaseWebSearchProvider):
    NAME = "firecrawl-local"
    DISPLAY_NAME = "Firecrawl (sandboxed self-host)"
    EXTRACT = True

    def is_available(self) -> bool:
        try:
            local_url()
            return True
        except ValueError:
            return False

    def supports_search(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        return {"success": False, "error": "firecrawl-local is extract-only; set web.search_backend: firecrawl"}

    async def _scrape(self, client: httpx.AsyncClient, base: str, url: str, fmt: str | None) -> Dict[str, Any]:
        if blocked := check_website_access(url):
            return _err(url, blocked["message"], blocked_by_policy={k: blocked[k] for k in ("host", "rule", "source")})
        if not is_safe_url(url):
            return _err(url, "Blocked: URL targets a private or internal network address")
        formats = [fmt] if fmt in ("markdown", "html") else ["markdown", "html"]
        try:
            r = await client.post(f"{base}/v2/scrape", json={"url": url, "formats": formats,
                                                            "timeout": (SCRAPE_TIMEOUT - 5) * 1000})
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return _err(url, f"firecrawl-local unreachable or bad reply: {type(e).__name__}")
        if not d.get("success"):
            return _err(url, str(d.get("error") or f"HTTP {r.status_code}")[:300])
        data = d.get("data") or {}
        meta = data.get("metadata") or {}
        final = meta.get("url") or meta.get("sourceURL") or url
        if not is_safe_url(final):
            return _err(final, "Blocked: URL targets a private or internal network address")
        if blocked := check_website_access(final):
            return _err(final, blocked["message"], blocked_by_policy={k: blocked[k] for k in ("host", "rule", "source")})
        markdown, html = data.get("markdown"), data.get("html")
        content = markdown if fmt == "markdown" or (fmt is None and markdown) else html or markdown or ""
        return {"url": final, "title": meta.get("title", ""), "content": content, "raw_content": content,
                "metadata": {k: meta.get(k) for k in ("title", "description", "statusCode", "url", "sourceURL")}}

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        from tools.interrupt import is_interrupted
        try:
            base = local_url()
        except ValueError as e:
            return [_err(u, str(e)) for u in urls]
        out = []
        async with httpx.AsyncClient(timeout=SCRAPE_TIMEOUT, trust_env=False) as client:
            for u in urls:
                if is_interrupted():
                    out.append(_err(u, "Interrupted"))
                    continue
                try:
                    out.append(await asyncio.wait_for(self._scrape(client, base, u, kwargs.get("format")), SCRAPE_TIMEOUT))
                except asyncio.TimeoutError:
                    out.append(_err(u, f"Scrape timed out after {SCRAPE_TIMEOUT}s"))
        return out

    def get_setup_schema(self) -> Dict[str, Any]:
        return setup_schema("Firecrawl (sandboxed self-host)", "local · extract only",
                            "web_extract through hermes-deploy/firecrawl on 127.0.0.1:3002.")
