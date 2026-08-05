"""
股票代碼 → 公司資訊查詢（讓所有 agent 都知道現在分析誰）
中文名改用 FinMind TaiwanStockInfo，避免 yfinance 只有英文名
"""
import os
import math
import requests
import yfinance as yf
from crewai.tools import BaseTool

FINMIND_BASE = "https://api.finmindtrade.com/api/v4"

def _get_stock_data_smart(stock_id: str):
    stock_id = stock_id.strip()
    if stock_id.endswith((".TW", ".TWO")):
        candidates = [stock_id]
    else:
        candidates = [stock_id + ".TW", stock_id + ".TWO"]

    for ticker in candidates:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            if info and info.get("longName"):
                return ticker, info
        except Exception:
            continue
    return None, None


def _get_chinese_name(stock_id: str):
    """從 FinMind TaiwanStockInfo 取得中文公司名稱"""
    stock_id = stock_id.strip().split(".")[0]
    token = os.getenv("FINMIND_TOKEN", "")
    if not token:
        return None
    try:
        r = requests.get(
            FINMIND_BASE + "/data",
            params={"dataset": "TaiwanStockInfo", "data_id": stock_id, "token": token},
            timeout=15,
        )
        data = r.json()
        if data.get("status") == 200 and data.get("data"):
            return data["data"][0].get("stock_name")
    except Exception:
        pass
    return None


def _clean_nan(value, default="N/A"):
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    return value


class StockResolverTool(BaseTool):
    name: str = "stock_identity_resolver"
    description: str = (
        "Input a Taiwan stock_id (e.g. 2327, 2330, 1815). "
        "Returns the exact company Chinese name, English name, "
        "and market type. This MUST be called first by every agent "
        "so they know which company they are analyzing. "
        "Supports both TWSE (上市) and TPEx (上櫃) stocks."
    )

    def _run(self, stock_id: str) -> str:
        ticker, info = _get_stock_data_smart(stock_id)
        if not ticker:
            return (
                "Cannot find " + stock_id + ". "
                "Please verify: 1) correct stock_id "
                "2) stock is listed on TWSE or TPEx"
            )

        zh_name = _get_chinese_name(stock_id)
        if not zh_name:
            zh_name = "N/A (FinMind 查無中文名)"
        en_name = _clean_nan(
            info.get("longName") or info.get("shortName"), "Unknown"
        )
        industry = _clean_nan(info.get("industry"), "Unknown")
        sector = _clean_nan(info.get("sector"), "")
        market = "TWSE (Listed)" if ticker.endswith(".TW") else "TPEx (OTC)"

        return (
            "Stock Identity: " + stock_id + "\n"
            "Market: " + market + "\n"
            "Chinese Name: " + zh_name + "\n"
            "English Name: " + str(en_name) + "\n"
            "Industry: " + str(industry) + "\n"
            "Sector: " + str(sector) + "\n"
        )
