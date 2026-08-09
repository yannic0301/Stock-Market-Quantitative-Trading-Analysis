"""
Web research tools for the news/research agents.

Design goals:
- Keep structured market/fundamental data sources unchanged.
- Turn article URLs into LLM-friendly full text instead of relying on headlines.
- Prefer Jina Reader for a fast, low-friction extraction path.
- Fall back to Crawl4AI for JS-heavy pages or when Jina is unavailable.
- Never invent content: failures are returned explicitly as "資料不足".
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional
from urllib.parse import quote

import requests
from crewai.tools import BaseTool


DEFAULT_TIMEOUT = int(os.getenv("WEB_RESEARCH_TIMEOUT", "30"))
MAX_CHARS = int(os.getenv("WEB_RESEARCH_MAX_CHARS", "12000"))


def _truncate(text: str, max_chars: int = MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[內容已截斷；如需更多細節可再次讀取原文。]"


def _jina_read(url: str) -> str:
    endpoint = f"https://r.jina.ai/{url}"
    headers = {
        "Accept": "text/plain",
        "User-Agent": "stock-quant-research-agent/1.0",
    }
    api_key = os.getenv("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.get(endpoint, headers=headers, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    content = response.text.strip()
    if not content:
        raise RuntimeError("Jina Reader returned empty content")
    return content


async def _crawl4ai_read_async(url: str) -> str:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    browser_cfg = BrowserConfig(
        browser_type="chromium",
        headless=True,
        verbose=False,
    )
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        word_count_threshold=10,
        remove_overlay_elements=True,
        page_timeout=DEFAULT_TIMEOUT * 1000,
        check_robots_txt=True,
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
        if not result.success:
            raise RuntimeError(result.error_message or "Crawl4AI crawl failed")
        content = (result.markdown or result.cleaned_html or "").strip()
        if not content:
            raise RuntimeError("Crawl4AI returned empty content")
        return content


def _crawl4ai_read(url: str) -> str:
    return asyncio.run(_crawl4ai_read_async(url))


class WebPageReaderTool(BaseTool):
    name: str = "web_page_reader"
    description: str = (
        "Read the full content of a web page from a URL. "
        "Prefer this after a news/search tool returns an article link. "
        "The tool first uses Jina Reader and automatically falls back to Crawl4AI "
        "for pages that need browser rendering. Input: url (str)."
    )

    def _run(self, url: str = "", **kwargs) -> str:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return "資料不足：web_page_reader 需要有效的 http/https URL。"

        jina_error: Optional[str] = None
        try:
            content = _jina_read(url)
            return (
                f"[WEB SOURCE]\nURL: {url}\nExtractor: Jina Reader\n\n"
                f"{_truncate(content)}"
            )
        except Exception as exc:
            jina_error = f"{type(exc).__name__}: {exc}"

        try:
            content = _crawl4ai_read(url)
            return (
                f"[WEB SOURCE]\nURL: {url}\nExtractor: Crawl4AI fallback\n\n"
                f"{_truncate(content)}"
            )
        except Exception as exc:
            return (
                "資料不足：無法讀取此網頁。\n"
                f"URL: {url}\n"
                f"Jina Reader error: {jina_error}\n"
                f"Crawl4AI error: {type(exc).__name__}: {exc}"
            )


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "Search the web using Jina Search and return LLM-friendly search results. "
        "Use this for targeted research questions when the existing news tools do not provide enough evidence. "
        "Requires JINA_API_KEY. Input: query (str)."
    )

    def _run(self, query: str = "", **kwargs) -> str:
        query = (query or "").strip()
        if not query:
            return "資料不足：web_search 需要 query。"

        api_key = os.getenv("JINA_API_KEY")
        if not api_key:
            return "資料不足：web_search 需要設定 JINA_API_KEY；目前可使用 web_page_reader 讀取已知 URL。"

        try:
            endpoint = f"https://s.jina.ai/?q={quote(query)}"
            response = requests.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "text/plain",
                    "User-Agent": "stock-quant-research-agent/1.0",
                },
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            content = response.text.strip()
            if not content:
                return "資料不足：web_search 沒有回傳結果。"
            return _truncate(content)
        except Exception as exc:
            return f"資料不足：web_search 失敗：{type(exc).__name__}: {exc}"
