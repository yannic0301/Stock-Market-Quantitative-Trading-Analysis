"""
新聞情緒分類工具：用 Zen（OpenAI 相容）給每則新聞貼標籤
不再依賴本機 ollama
"""
import os
import requests
from crewai.tools import BaseTool

class NewsSentimentClassifierTool(BaseTool):
    name: str = "news_sentiment_classifier"
    description: str = (
        "Input news headlines (one per line). "
        "Output sentiment labels (POSITIVE/NEGATIVE/NEUTRAL) for each."
    )

    def _run(self, news_text: str) -> str:
        base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/v1").rstrip("/")
        key = os.getenv("OPENAI_API_KEY", "")
        if not key or key == "YOUR_ZEN_API_KEY_HERE":
            return "Sentiment analysis failed: OPENAI_API_KEY not set"

        prompt = (
            "You are a financial news sentiment classifier. "
            "For each headline, output one line:\n"
            "[LABEL] Headline text\n\n"
            "LABEL must be: POSITIVE, NEGATIVE, or NEUTRAL.\n\n"
            "News:\n" + news_text
        )

        for model in ["deepseek-v4-flash-free", "ling-3.0-flash-free"]:
            try:
                r = requests.post(
                    base + "/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                    },
                    timeout=60,
                )
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"]
            except Exception:
                continue
        return "Sentiment analysis failed: no model available"
