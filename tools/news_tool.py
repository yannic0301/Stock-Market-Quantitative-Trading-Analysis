"""
新聞工具 - 修正版
所有 _run() 接受 **kwargs，避免 LLM 亂傳參數
"""
import feedparser
import re
from datetime import datetime
from crewai.tools import BaseTool


def _clean_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()


class TWStockNewsTool(BaseTool):
    name: str = "tw_stock_news_search"
    description: str = "Search Taiwan stock news by company name. Input: query (str)."

    def _run(self, query: str = "台股") -> str:
        try:
            url = f"https://news.google.com/rss/search?q={query}+台股&hl=zh-TW&gl=TW"
            feed = feedparser.parse(url)
            if not feed.entries:
                return f"No news found for {query}"
            header = f"[{query} related news, {min(10, len(feed.entries))} items]\n"
            lines = [header]
            for i, e in enumerate(feed.entries[:10], 1):
                pub = e.get("published", "")
                if pub:
                    try:
                        pub = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").strftime("%Y-%m-%d")
                    except Exception:
                        pass
                source = e.get("source", {}).get("title", "unknown")
                lines.append(f"{i}. [{pub}] {source}")
                lines.append(f"   Title: {_clean_html(e.title)}")
                lines.append(f"   Link: {e.link}\n")
            return "\n".join(lines)
        except Exception as e:
            return f"Search failed: {e}"


class TWMarketNewsTool(BaseTool):
    name: str = "tw_market_news_search"
    description: str = "Get Taiwan market index news. No input needed. Returns 10 items."

    def _run(self, **kwargs) -> str:  # ← 接受任意參數避免報錯
        try:
            url = "https://news.google.com/rss/search?q=台股+加權指數&hl=zh-TW&gl=TW"
            feed = feedparser.parse(url)
            if not feed.entries:
                return "No market news"
            header = f"[Taiwan market news, {min(10, len(feed.entries))} items]\n"
            lines = [header]
            for i, e in enumerate(feed.entries[:10], 1):
                lines.append(f"{i}. {_clean_html(e.title)}")
                lines.append(f"   {e.link}\n")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed: {e}"


class TWIndustryNewsTool(BaseTool):
    name: str = "tw_industry_news_search"
    description: str = "Get industry news by keyword. Input: industry (str, e.g. 半導體)."

    def _run(self, industry: str = "電子") -> str:
        try:
            url = f"https://news.google.com/rss/search?q={industry}+產業&hl=zh-TW&gl=TW"
            feed = feedparser.parse(url)
            if not feed.entries:
                return f"No {industry} industry news"
            header = f"[{industry} industry news, {min(10, len(feed.entries))} items]\n"
            lines = [header]
            for i, e in enumerate(feed.entries[:10], 1):
                lines.append(f"{i}. {_clean_html(e.title)}")
                lines.append(f"   {e.link}\n")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed: {e}"


class IntlMarketNewsTool(BaseTool):
    name: str = "international_market_news_search"
    description: str = "Get international financial news. No input needed."

    def _run(self, **kwargs) -> str:
        try:
            url = "https://news.google.com/rss/search?q=美股+聯準會+半導體&hl=zh-TW&gl=TW"
            feed = feedparser.parse(url)
            if not feed.entries:
                return "No international news"
            header = f"[International news, {min(10, len(feed.entries))} items]\n"
            lines = [header]
            for i, e in enumerate(feed.entries[:10], 1):
                lines.append(f"{i}. {_clean_html(e.title)}")
                lines.append(f"   {e.link}\n")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed: {e}"
