"""Register the ``firecrawl-local`` web extract provider (see provider.py)."""
from __future__ import annotations


def register(ctx) -> None:
    from .provider import FirecrawlLocalProvider
    ctx.register_web_search_provider(FirecrawlLocalProvider())
