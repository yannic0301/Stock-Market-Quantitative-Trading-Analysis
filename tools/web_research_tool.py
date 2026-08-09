"""Free-first web research tools.

Phase 1: read a specific URL with Jina Reader + direct HTTP fallback.
Phase 2: run a small multi-query research pass using Google News RSS, rank and
 deduplicate candidates, read several articles, and return an evidence dossier.

No paid search API, API key, Crawl4AI, or Browser Use is required.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool

_TIMEOUT = int(os.getenv("WEB_READER_TIMEOUT", "20"))
_MAX_CHARS = int(os.getenv("WEB_READER_MAX_CHARS", "18000"))
_RESEARCH_ARTICLES = int(os.getenv("WEB_RESEARCH_ARTICLES", "5"))
_RESEARCH_CANDIDATES = int(os.getenv("WEB_RESEARCH_CANDIDATES", "8"))
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
    response = requests.get(
        "https://r.jina.ai/" + url,
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
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
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
    return _trim(f"# {title}\n\n{body}" if title else body)


def _read_url(url: str) -> tuple[str, str]:
    """Return (text, backend). Never invent article content."""
    jina_error = None
    if _JINA_ENABLED:
        try:
            return _extract_with_jina(url), "Jina Reader"
        except Exception as exc:
            jina_error = f"Jina: {type(exc).__name__}: {exc}"
    try:
        return _extract_direct(url), "direct HTTP"
    except Exception as exc:
        detail = f"direct HTTP: {type(exc).__name__}: {exc}"
        if jina_error:
            detail = jina_error + " | " + detail
        raise RuntimeError(detail) from exc


@dataclass
class Candidate:
    title: str
    url: str
    source: str
    published: str
    query_type: str


def _search_google_news(query: str, query_type: str, limit: int = 8) -> list[Candidate]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)
    results: list[Candidate] = []
    for entry in feed.entries[:limit]:
        source = entry.get("source", {}).get("title", "unknown")
        published = entry.get("published", "")
        if published:
            try:
                published = datetime.strptime(
                    published, "%a, %d %b %Y %H:%M:%S %Z"
                ).strftime("%Y-%m-%d")
            except Exception:
                pass
        title = re.sub(r"<[^>]+>", "", entry.get("title", "")).strip()
        link = entry.get("link", "").strip()
        if title and link:
            results.append(Candidate(title, link, source, published, query_type))
    return results


def _dedupe_candidates(items: list[Candidate]) -> list[Candidate]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[Candidate] = []
    for item in items:
        title_key = re.sub(r"\W+", "", item.title.lower())
        if item.url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(item.url)
        seen_titles.add(title_key)
        out.append(item)
    return out


class WebPageReaderTool(BaseTool):
    name: str = "web_page_reader"
    description: str = (
        "Read a specific news/article/announcement URL and return its main text. "
        "Input: url (str). Use this after a news search tool returns an article link. "
        "Prefer primary sources, company announcements, government sources, and full articles."
    )

    def _run(self, url: str = "") -> str:
        try:
            url = _normalise_url(url)
            text, backend = _read_url(url)
            return f"[WEB PAGE]\nURL: {url}\nSOURCE: {backend}\n\n{text}"
        except Exception as exc:
            return f"資料不足：{type(exc).__name__}: {exc}"


class DeepWebResearchTool(BaseTool):
    """Run a free multi-query research pass and return an evidence dossier."""

    name: str = "deep_web_research"
    description: str = (
        "Run a focused web research pass for a stock/company. Input is one JSON-like text: "
        "company, industry, and optional focus. It searches several independent Google News "
        "queries, deduplicates candidates, reads the strongest articles with Jina/direct HTTP, "
        "and returns source metadata plus article text. No API key required."
    )

    def _run(self, company: str = "", industry: str = "", focus: str = "") -> str:
        company = company.strip()
        industry = industry.strip() or "台股"
        focus = focus.strip() or "近期股價與基本面可能有影響的事件"
        if not company:
            return "資料不足：company is required"

        queries = [
            (f"{company} 台股 最新 消息", "company"),
            (f"{company} 法說會 OR 財報 OR 營收", "company_primary"),
            (f"{company} {industry} 產業", "industry"),
            (f"台股 {industry} 最新", "taiwan_market"),
            (f"美股 半導體 聯準會 AI {industry}", "international"),
            (f"{company} {focus}", "focus"),
        ]

        candidates: list[Candidate] = []
        for query, query_type in queries:
            try:
                candidates.extend(_search_google_news(query, query_type, limit=_RESEARCH_CANDIDATES))
            except Exception:
                continue
        candidates = _dedupe_candidates(candidates)

        # Diversify by query type/source before filling remaining slots.
        selected: list[Candidate] = []
        used_types: set[str] = set()
        used_sources: set[str] = set()
        for item in candidates:
            if item.query_type not in used_types or item.source not in used_sources:
                selected.append(item)
                used_types.add(item.query_type)
                used_sources.add(item.source)
            if len(selected) >= _RESEARCH_ARTICLES:
                break
        if len(selected) < _RESEARCH_ARTICLES:
            for item in candidates:
                if item not in selected:
                    selected.append(item)
                if len(selected) >= _RESEARCH_ARTICLES:
                    break

        if not selected:
            return "資料不足：multi-query web search returned no candidates"

        lines = [
            "[DEEP WEB RESEARCH DOSSIER]",
            f"Company: {company}",
            f"Industry: {industry}",
            f"Research focus: {focus}",
            f"Candidate articles found: {len(candidates)}",
            f"Articles read: {len(selected)}",
            "",
            "【Evidence rule】Only statements explicitly present in the article text may be treated as article evidence.",
            "",
        ]

        successful = 0
        for idx, item in enumerate(selected, 1):
            lines.extend([
                f"===== SOURCE {idx} =====",
                f"Date: {item.published or 'unknown'}",
                f"Source: {item.source}",
                f"Query category: {item.query_type}",
                f"Title: {item.title}",
                f"URL: {item.url}",
            ])
            try:
                text, backend = _read_url(item.url)
                successful += 1
                lines.append(f"Reader: {backend}")
                lines.append("Article text:")
                lines.append(text)
            except Exception as exc:
                lines.append(f"Article text: 資料不足（無法讀取原文：{type(exc).__name__}: {exc}）")
            lines.append("")

        lines.extend([
            "===== RESEARCH STATUS =====",
            f"Successfully read: {successful}/{len(selected)}",
            "Do not treat unreadable articles as evidence. Use title/source/date only for those entries.",
        ])
        return "\n".join(lines)
