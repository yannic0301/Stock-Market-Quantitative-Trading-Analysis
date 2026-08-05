"""
FinMind 工具集：台股專屬資料（免費 600 次/天）
涵蓋：法人買賣超、月營收、本益比、融資融券、股價
"""
import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from crewai.tools import BaseTool

FINMIND_BASE = "https://api.finmindtrade.com/api/v4"

INST_NAME_MAP = {
    "Foreign_Investor": "外資",
    "Investment_Trust": "投信",
    "Dealer_self": "自營(自行買賣)",
    "Dealer_Hedging": "自營(避險)",
    "Foreign_Dealer_Self": "外資自營",
}


def _finmind_query(dataset: str, stock_id: str, start_date: str = None, end_date: str = None):
    """通用 finmind 查詢函式"""
    token = os.getenv("FINMIND_TOKEN", "")
    if not token:
        return None, "FINMIND_TOKEN not set in .env"

    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "token": token,
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    try:
        r = requests.get(FINMIND_BASE + "/data", params=params, timeout=15)
        data = r.json()
        if data.get("status") != 200:
            return None, data.get("msg", "unknown error")
        return data.get("data", []), None
    except Exception as e:
        return None, str(e)


class FinmindInstitutionalTool(BaseTool):
    name: str = "finmind_institutional_investors"
    description: str = (
        "Get Taiwan stock institutional investor buy/sell data: "
        "Foreign Investor, Investment Trust, Dealer. "
        "Input: stock_id (e.g. 2327). "
        "Returns last 5 trading days net buy/sell in NTD."
    )

    def _run(self, stock_id: str) -> str:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        data, err = _finmind_query(
            "TaiwanStockInstitutionalInvestorsBuySell",
            stock_id, start, end
        )
        if err:
            return "FinMind query failed: " + err
        if not data:
            return "No institutional data for " + stock_id

        df = pd.DataFrame(data)
        df["net"] = df["buy"] - df["sell"]
        pivot = df.pivot_table(
            index="date", columns="name", values="net", aggfunc="sum"
        ).fillna(0).sort_index()

        # 依固定順序輸出，欄位不存在則略過
        order = [
            "Foreign_Investor", "Investment_Trust",
            "Dealer_self", "Dealer_Hedging",
        ]
        present = [c for c in order if c in pivot.columns]
        summary_rows = []
        for date, r in pivot.tail(5).iterrows():
            parts = [str(date)]
            for col in present:
                parts.append(
                    INST_NAME_MAP.get(col, col) + ":" + str(int(r[col]))
                )
            summary_rows.append(" | ".join(parts))
        return "[Institutional Investors - " + stock_id + " last 5 days]\n" + "\n".join(summary_rows)


class FinmindMonthlyRevenueTool(BaseTool):
    name: str = "finmind_monthly_revenue"
    description: str = (
        "Get Taiwan stock monthly revenue with year-over-year growth. "
        "Input: stock_id (e.g. 2327). "
        "Returns last 12 months revenue (million NTD) and YoY growth rate."
    )

    def _run(self, stock_id: str) -> str:
        # 抓 24 個月以上資料，才能比對「去年同期」計算真實 YoY
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=760)).strftime("%Y-%m-%d")
        data, err = _finmind_query(
            "TaiwanStockMonthRevenue",
            stock_id, start, end
        )
        if err:
            return "FinMind query failed: " + err
        if not data:
            return "No monthly revenue data for " + stock_id

        df = pd.DataFrame(data)
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        df["ym"] = df["date"].astype(str).str[:7]
        rev_by_ym = dict(zip(df["ym"], df["revenue"]))

        rows = []
        for _, r in df.tail(12).iterrows():
            ym = r["ym"]
            rev = r["revenue"]
            y, m = int(ym[:4]), ym[5:7]
            prev_ym = f"{y - 1}-{m}"
            prev_rev = rev_by_ym.get(prev_ym)
            if prev_rev:
                yoy = str(round((rev - prev_rev) / prev_rev * 100, 1)) + "%"
            else:
                yoy = "N/A"
            rows.append(
                ym + " | 營收:"
                + str(round(rev / 1e6, 1)) + "億 | YoY:" + yoy
            )
        return "[Monthly Revenue - " + stock_id + "]\n" + "\n".join(rows)


class FinmindEPSNetProfitTool(BaseTool):
    name: str = "finmind_eps_net_profit"
    description: str = (
        "Get Taiwan stock actual quarterly EPS and trailing-4-quarter TTM EPS "
        "from FinMind official financial statements. "
        "Input: stock_id (e.g. 6214). "
        "Returns last 6 quarters EPS, TTM EPS, and latest quarter YoY growth. "
        "This is the authoritative source for EPS (more reliable than Yahoo)."
    )

    def _run(self, stock_id: str) -> str:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=950)).strftime("%Y-%m-%d")
        data, err = _finmind_query(
            "TaiwanStockFinancialStatements", stock_id, start, end
        )
        if err:
            return "FinMind query failed: " + err
        if not data:
            return "No EPS data for " + stock_id

        df = pd.DataFrame(data)
        if df.empty:
            return "No EPS data for " + stock_id

        df["date"] = pd.to_datetime(df["date"])
        eps = df[df["type"] == "EPS"].copy()
        if eps.empty:
            return "No EPS records for " + stock_id

        eps = eps.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        eps = eps.tail(6)

        rows = []
        for _, r in eps.iterrows():
            rows.append(str(r["date"].date()) + " | 單季 EPS:" + str(r["value"]))

        # TTM = 近四季合計
        vals = eps["value"]
        ttm = round(vals.tail(4).sum(), 2)
        latest_eps = float(eps["value"].iloc[-1])

        # 最新一季 YoY：找一年前同季
        latest_date = eps["date"].iloc[-1]
        prev_year = latest_date.replace(year=latest_date.year - 1)
        full = df[df["type"] == "EPS"].copy().sort_values("date")
        prev_rows = full[full["date"] == prev_year]
        yoy = "N/A"
        if not prev_rows.empty and prev_rows["value"].iloc[0] not in (None, 0, 0.0):
            prev_eps = float(prev_rows["value"].iloc[0])
            yoy = str(round((latest_eps - prev_eps) / prev_eps * 100, 1)) + "%"

        result = (
            "[FinMind EPS (official financial statements) - " + stock_id + "]\n"
            + "近6季單季EPS:\n" + "\n".join(rows) + "\n"
            + "實際 TTM EPS (近四季合計): " + str(ttm) + "\n"
            + "最新一季 EPS: " + str(latest_eps) + "\n"
            + "最新一季 YoY: " + yoy + "\n"
            + "註: 此為FinMind官方財報實際值，優先於Yahoo預估值"
        )
        return result


MIN_PER_SAMPLES = 60  # 三情境百分位所需之最低有效樣本數（約一季交易日）


class FinmindPERTool(BaseTool):
    name: str = "finmind_pe_ratio_history"
    description: str = (
        "Get Taiwan stock historical PER and PBR from FinMind (~2 years). "
        "Input: stock_id (e.g. 2327). "
        "Returns sample count + date span + percentiles P25/P50/P75 and "
        "last 10 trading days detail. If fewer than 60 valid samples, "
        "returns 資料不足 so agents must not compute target PEs."
    )

    def _run(self, stock_id: str) -> str:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=760)).strftime("%Y-%m-%d")
        data, err = _finmind_query(
            "TaiwanStockPER",
            stock_id, start, end
        )
        if err:
            return "FinMind query failed: " + err
        if not data:
            return "No PER data for " + stock_id

        df = pd.DataFrame(data)
        if "PER" not in df.columns or df["PER"].isna().all():
            return "No valid PER data for " + stock_id

        valid = df[df["PER"].notna() & (df["PER"] > 0)].copy()
        valid["date"] = valid["date"].astype(str)
        valid = valid.sort_values("date").drop_duplicates(subset=["date"], keep="last")

        n = len(valid)
        if n == 0:
            return "No valid PER data for " + stock_id
        span_start = str(valid["date"].iloc[0])
        span_end = str(valid["date"].iloc[-1])

        if n < MIN_PER_SAMPLES:
            return (
                "[PER History - " + stock_id + "] 資料不足：近兩年僅 " + str(n)
                + " 筆有效 PER（" + span_start + " ~ " + span_end
                + "），樣本不足以計算三情境目標本益比，禁止套用百分位推算目標價。"
            )

        p25 = valid["PER"].quantile(0.25)
        p50 = valid["PER"].quantile(0.50)
        p75 = valid["PER"].quantile(0.75)
        avg_per = valid["PER"].mean()
        max_per = valid["PER"].max()
        min_per = valid["PER"].min()

        rows = []
        for _, r in valid.tail(10).iterrows():
            pbr = r.get("PBR")
            pbr_s = str(round(pbr, 2)) if pbr is not None else "N/A"
            rows.append(
                str(r["date"]) + " | PER:"
                + str(round(r["PER"], 2)) + " | PBR:" + pbr_s
            )
        result = (
            "[PER History - " + stock_id + "]\n"
            + "樣本數: " + str(n) + "（" + span_start + " ~ " + span_end + "）\n"
            + "近2年平均 PER: " + str(round(avg_per, 2)) + "\n"
            + "區間: " + str(round(min_per, 2)) + " ~ " + str(round(max_per, 2)) + "\n"
            + "百分位: P25 " + str(round(p25, 2))
            + " / P50 " + str(round(p50, 2))
            + " / P75 " + str(round(p75, 2)) + "\n\n"
            + "近10日數據:\n" + "\n".join(rows)
        )
        return result


class FinmindMarginTool(BaseTool):
    name: str = "finmind_margin_trading"
    description: str = (
        "Get Taiwan stock margin trading (融資融券) data. "
        "Input: stock_id (e.g. 2327). "
        "Returns last 5 days margin balance and change."
    )

    def _run(self, stock_id: str) -> str:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        data, err = _finmind_query(
            "TaiwanStockMarginPurchaseShortSale",
            stock_id, start, end
        )
        if err:
            return "FinMind query failed: " + err
        if not data:
            return "No margin data for " + stock_id

        df = pd.DataFrame(data).tail(5)
        rows = []
        for _, r in df.iterrows():
            rows.append(
                str(r.get("date", "")) + " | 融資買進:"
                + str(r.get("MarginPurchaseBuy", 0)) + " | 融資賣出:"
                + str(r.get("MarginPurchaseSell", 0)) + " | 融券買進:"
                + str(r.get("ShortSaleBuy", 0)) + " | 融券賣出:"
                + str(r.get("ShortSaleSell", 0))
            )
        return "[Margin Trading - " + stock_id + " last 5 days]\n" + "\n".join(rows)


class FinmindPriceTool(BaseTool):
    name: str = "finmind_stock_price"
    description: str = (
        "Get Taiwan stock daily price with MA from FinMind. "
        "Input: stock_id (e.g. 2327). "
        "Returns last 30 trading days OHLCV + MA5/MA10/MA20."
    )

    def _run(self, stock_id: str) -> str:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        data, err = _finmind_query(
            "TaiwanStockPrice",
            stock_id, start, end
        )
        if err:
            return "FinMind query failed: " + err
        if not data:
            return "No price data for " + stock_id

        df = pd.DataFrame(data)
        df["MA5"] = df["close"].rolling(5).mean()
        df["MA10"] = df["close"].rolling(10).mean()
        df["MA20"] = df["close"].rolling(20).mean()
        last = df.tail(10)
        rows = []
        for _, r in last.iterrows():
            rows.append(
                str(r["date"]) + " | 收:"
                + str(r["close"]) + " | MA5:"
                + str(round(r["MA5"], 2) if pd.notna(r["MA5"]) else 0) + " | 量:"
                + str(int(r["Trading_Volume"]))
            )
        return "[Stock Price - " + stock_id + " last 10 days]\n" + "\n".join(rows)


class FinmindShareholdingTool(BaseTool):
    name: str = "finmind_shareholding"
    description: str = (
        "Get Taiwan stock shareholding distribution: percentage held by "
        "directors, major shareholders, and public. "
        "Input: stock_id (e.g. 2327). "
        "Returns latest data showing ownership concentration."
    )

    def _run(self, stock_id: str) -> str:
        data, err = _finmind_query(
            "TaiwanStockShareholding",
            stock_id
        )
        if err:
            return "FinMind query failed: " + err
        if not data:
            return "No shareholding data for " + stock_id

        df = pd.DataFrame(data)
        if df.empty:
            return "Empty shareholding data"
        latest = df.iloc[-1]
        return (
            "[Shareholding - " + stock_id + " latest]\n"
            "Date: " + str(latest.get("date", "")) + "\n"
            "Director ratio: " + str(latest.get("Director", "N/A")) + "%\n"
            "Major shareholder ratio: " + str(latest.get("Major", "N/A")) + "%\n"
            "Public ratio: " + str(latest.get("Public", "N/A")) + "%\n"
        )
