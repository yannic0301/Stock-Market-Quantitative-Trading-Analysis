"""Free-first web page reader for the news research agent.

Backends:
1. Jina Reader public endpoint (no API key required, subject to free rate limits).
2. Direct HTTP + BeautifulSoup fallback when Jina is unavailable.

This module deliberately does NOT require Crawl4AI, Browser Use, or a paid API.
Those can be added later as optional backends if needed.
"""

from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool


_TIMEOUT = int(os.getenv("WEB_READER_TIMEOUT", "20"))
_MAX_CHARS = int(os.getenv("WEB_READER_MAX_CHARS", "18000"))
_JINA_ENABLED = os.getenv("JINA_READER_ENABLED", "true").lower() not in {"0", "false", "no"}


def _normalise_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only http/https URLs are supported")
    return url


def _trim(text: str, limit: int = _MAX_CHARS) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[內容已截斷：超過單頁閱讀上限]"


def _extract_with_jina(url: str) -> str:
    endpoint = "https://r.jina.ai/" + url
    response = requests.get(
        endpoint,
        timeout=_TIMEOUT,
        headers={"Accept": "text/plain", "User-Agent": "stock-quant-research/1.0"},
    )
    response.raise_for_status()
    content = response.text.strip()
    if not content:
        raise RuntimeError("Jina Reader returned empty content")
    return _trim(content)


def _extract_direct(url: str) -> str:
    response = requests.get(
        url,
        timeout=_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "text/plain" not in content_type:
        return _trim(response.text)

    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        node.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.get_text("\n", strip=True)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)

    if title:
        body = f"# {title}\n\n{body}"
    return _trim(body)


class WebPageReaderTool(BaseTool):
    """Read a web page into LLM-friendly text with a free-first strategy."""

    name: str = "web_page_reader"
    description: str = (
        "Read a specific news/article/announcement URL and return its main text. "
        "Input: url (str). Use this after a news search tool returns an article link. "
        "Prefer primary sources, company announcements, government sources, and full articles."
    )

    def _run(self, url: str = "") -> str:
        try:
            url = _normalise_url(url)
        except Exception as exc:
            return f"Web page read failed: {exc}"

        jina_error: Optional[str] = None
        if _JINA_ENABLED:
            try:
                text = _extract_with_jina(url)
                return f"[WEB PAGE]\nURL: {url}\nSOURCE: Jina Reader (free public endpoint)\n\n{text}"
            except Exception as exc:
                jina_error = f"Jina Reader failed: {type(exc).__name__}: {exc}"

        try:
            text = _extract_direct(url)
            return f"[WEB PAGE]\nURL: {url}\nSOURCE: direct HTTP fallback\n\n{text}"
        except Exception as exc:
            details = f"Direct reader failed: {type(exc).__name__}: {exc}"
            if jina_error:
                details = jina_error + " | " + details
            return f"資料不足：{details}"
