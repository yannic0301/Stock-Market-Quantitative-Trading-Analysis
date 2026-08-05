"""
Taiwan stock tools - 修正版
1. 智慧判斷上市/上櫃
2. 處理 yfinance 回傳 nan 的情況
3. 跳過非交易日
"""
import yfinance as yf
from crewai.tools import BaseTool
import math
import datetime


def _get_stock_data_smart(stock_id: str):
    """智慧判斷上市/上櫃，回傳 (ticker, info_dict)"""
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


def _clean_nan(value, default="N/A"):
    """處理 NaN 值"""
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    return value


def _patch_latest_close(hist: "pd.DataFrame", ticker: str) -> "pd.DataFrame":
    """Yahoo chart API 偶發對最新交易日回傳 close=None（如 8/3 大盤/個股），
    導致 dropna 後資料停在上一交易日（工具回傳過時資料）。
    修復：(1) 若最後一筆 Close 為 NaN，用 Ticker.info 的 regularMarketPrice 補正；
    (2) 若最後一筆日期 < 最新交易日（auto_adjust=True 時該日整列被內部丟棄），
    改用 auto_adjust=False 重抓一次再補正。"""
    if hist is None or hist.empty:
        return hist

    # 取最新交易日日期（regularMarketTime），用於比對
    latest_day = None
    try:
        mt = yf.Ticker(ticker).info.get("regularMarketTime")
        if mt:
            latest_day = datetime.datetime.fromtimestamp(
                mt, datetime.timezone(datetime.timedelta(hours=8))
            ).date()
    except Exception:
        pass

    # 若最後一筆日期早於最新交易日，重抓 auto_adjust=False（保留 close=None 的那行）
    if latest_day is not None:
        last_date = hist.index[-1].tz_convert("Asia/Taipei").date()
        if last_date < latest_day:
            try:
                hist2 = yf.Ticker(ticker).history(
                    period="1y", auto_adjust=False
                )
                if hist2 is not None and not hist2.empty:
                    hist = hist2
            except Exception:
                pass

    last_close = hist["Close"].iloc[-1]
    if not (isinstance(last_close, float) and math.isnan(last_close)):
        return hist
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice")
        if price is None:
            return hist
        hist = hist.copy()
        idx = hist.index[-1]
        for col in ("Open", "High", "Low", "Close"):
            v = hist.loc[idx, col]
            if isinstance(v, float) and math.isnan(v):
                hist.loc[idx, col] = price
        return hist
    except Exception:
        return hist


def _calc_indicators(hist: "pd.DataFrame") -> dict:
    """從 OHLCV 歷史計算技術指標（MA60/RSI/MACD/KD/布林/成交量均量）"""
    c = hist["Close"]
    low = hist["Low"]
    high = hist["High"]
    vol = hist["Volume"]

    out = {}

    def _last(series, nd=2):
        v = series.iloc[-1] if len(series) else float("nan")
        c = _clean_nan(v)
        return "N/A" if c == "N/A" else round(c, nd)

    # MA60 季線
    out["MA60"] = _last(c.rolling(60).mean())

    # RSI(14) Wilder 平滑
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss
    rsi = 100 - 100 / (1 + rs)
    out["RSI"] = _last(rsi)

    # MACD(12,26,9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    out["DIF"] = _last(dif)
    out["DEA"] = _last(dea)
    out["MACD"] = _last(macd)

    # KD(9,3,3) 台股慣用
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (c - low9) / (high9 - low9) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    out["K"] = _last(k)
    out["D"] = _last(d)

    # 布林通道(20, 2σ)
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    out["BB_up"] = _last(mid + 2 * std)
    out["BB_mid"] = _last(mid)
    out["BB_low"] = _last(mid - 2 * std)

    # 成交量均量
    out["Vol_latest"] = _last(vol, nd=0)
    out["Vol_MA5"] = _last(vol.rolling(5).mean(), nd=0)
    out["Vol_MA20"] = _last(vol.rolling(20).mean(), nd=0)
    return out


class TWStockKLineTool(BaseTool):
    name: str = "tw_stock_kline_query"
    description: str = "Query Taiwan stock K-line with MA5/MA10/MA20/MA60, RSI, MACD, KD, Bollinger Bands, and volume averages. Input: stock_id."

    def _run(self, stock_id: str) -> str:
        try:
            ticker, info = _get_stock_data_smart(stock_id)
            if not ticker:
                return f"No data for {stock_id}"

            stock = yf.Ticker(ticker)
            hist = stock.history(period="1y")
            if hist.empty:
                return f"No price history for {ticker}"

            # 最新交易日收盤缺失時補正（Yahoo chart API 偶發 close=None）
            hist = _patch_latest_close(hist, ticker)

            # 移除 NaN 行
            hist = hist.dropna(subset=["Close"])

            if hist.empty:
                return f"All recent prices are NaN for {ticker} (likely non-trading day)"

            hist["MA5"] = hist["Close"].rolling(5).mean()
            hist["MA10"] = hist["Close"].rolling(10).mean()
            hist["MA20"] = hist["Close"].rolling(20).mean()

            ind = _calc_indicators(hist)

            latest = hist.iloc[-1]
            zh_name = info.get("longName", stock_id)

            def _fmt(v, nd=2):
                c = _clean_nan(v)
                return "N/A" if c == "N/A" else round(c, nd)

            last5 = [
                round(x, 2)
                for x in hist["Close"].tail(5).tolist()
                if not (isinstance(x, float) and math.isnan(x))
            ]

            return (
                f"[{zh_name} ({stock_id}) Technical Data]\n"
                f"Source: {ticker}\n"
                f"Latest close: {_fmt(latest['Close'])}\n"
                f"MA5: {_fmt(latest['MA5'])}\n"
                f"MA10: {_fmt(latest['MA10'])}\n"
                f"MA20: {_fmt(latest['MA20'])}\n"
                f"MA60 (季線): {ind['MA60']}\n"
                f"RSI(14): {ind['RSI']}\n"
                f"MACD: DIF {ind['DIF']} / DEA {ind['DEA']} / MACD柱 {ind['MACD']}\n"
                f"KD(9,3,3): K {ind['K']} / D {ind['D']}\n"
                f"Bollinger(20,2σ): 上軌 {ind['BB_up']} / 中軌 {ind['BB_mid']} / 下軌 {ind['BB_low']}\n"
                f"Volume: 最新 {ind['Vol_latest']} / MA5 {ind['Vol_MA5']} / MA20 {ind['Vol_MA20']}\n"
                f"Last 5 closes: {last5}\n"
                f"20D high: {round(hist['High'].tail(20).max(), 2)}\n"
                f"20D low: {round(hist['Low'].tail(20).min(), 2)}\n"
            )
        except Exception as e:
            return f"Query failed: {e}"


class TWFundamentalTool(BaseTool):
    name: str = "tw_fundamental_query"
    description: str = (
        "Query Taiwan stock basic profile info: company name, industry, "
        "market cap (from Yahoo, for identification only). EPS/PE/估值一律 "
        "改用 finmind_* 工具，勿用本工具。Input: stock_id."
    )

    def _run(self, stock_id: str) -> str:
        try:
            ticker, info = _get_stock_data_smart(stock_id)
            if not ticker:
                return f"No data for {stock_id}"

            return (
                f"[{info.get('longName', stock_id)} ({stock_id}) Fundamental]\n"
                f"Source: {ticker}\n"
                f"Industry: {_clean_nan(info.get('industry'))}\n"
                f"Market Cap: {_clean_nan(info.get('marketCap'))}\n"
                f"註: Yahoo 對台股的 EPS/PE/目標價常為過時值，已停用不回傳。"
                f"EPS 一律以 finmind_eps_net_profit 官方財報實際值為準，"
                f"PER 一律以 finmind_pe_ratio_history 為準。\n"
            )
        except Exception as e:
            return f"Query failed: {e}"


class TWMacroTool(BaseTool):
    name: str = "tw_macro_query"
    description: str = (
        "Query Taiwan macro environment: TWD exchange rate, US 10Y treasury "
        "yield, VIX fear index, and USD index. No input needed."
    )

    def _run(self, **kwargs) -> str:  # ← 接受任意參數避免報錯
        lines = ["[Taiwan Macro Environment]"]
        ok = False
        # (標籤, 代碼, 小數位)
        for label, ticker, nd in [
            ("台幣兌美元 (USD/TWD)", "TWD=X", 2),
            ("美債10年期殖利率 (US10Y)", "^TNX", 2),
            ("VIX 恐慌指數", "^VIX", 2),
            ("美元指數 (DXY)", "DX-Y.NYB", 2),
        ]:
            try:
                t = yf.Ticker(ticker)
                h = t.history(period="1mo")
                h = h.dropna(subset=["Close"])
                if h.empty:
                    lines.append(f"{label}: N/A (no data)")
                    continue
                last = h["Close"].iloc[-1]
                prev = h["Close"].iloc[-2] if len(h) > 1 else last
                chg = (last - prev) / prev * 100 if prev else 0.0
                lines.append(f"{label}: {round(last, nd)} ({chg:+.2f}% daily)")
                ok = True
            except Exception as e:
                lines.append(f"{label}: N/A (query failed: {e})")
        if not ok:
            return "No Taiwan macro data available."
        return "\n".join(lines)


class TWIndexTool(BaseTool):
    name: str = "tw_market_index_query"
    description: str = "Query Taiwan Weighted Index with MA5/MA10/MA20/MA60, RSI, MACD, KD, Bollinger Bands, and volume averages. No input needed."

    def _run(self, **kwargs) -> str:  # ← 接受任意參數
        try:
            for ticker in ["^TWII", "TWII"]:
                idx = yf.Ticker(ticker)
                hist = idx.history(period="1y")
                if not hist.empty:
                    break
            else:
                return "No Taiwan index data"

            hist = _patch_latest_close(hist, ticker)

            hist = hist.dropna(subset=["Close"])
            if hist.empty:
                return "Taiwan index has no valid prices (non-trading day)"

            hist["MA5"] = hist["Close"].rolling(5).mean()
            hist["MA10"] = hist["Close"].rolling(10).mean()
            hist["MA20"] = hist["Close"].rolling(20).mean()

            ind = _calc_indicators(hist)

            latest = hist.iloc[-1]
            month_change = (latest["Close"] - hist["Close"].iloc[0]) / hist["Close"].iloc[0] * 100

            def _fmt(v, nd=2):
                c = _clean_nan(v)
                return "N/A" if c == "N/A" else round(c, nd)

            last5 = [
                round(x, 2)
                for x in hist["Close"].tail(5).tolist()
                if not (isinstance(x, float) and math.isnan(x))
            ]

            return (
                f"[Taiwan Weighted Index]\n"
                f"Latest close: {_fmt(latest['Close'])}\n"
                f"MA5: {_fmt(latest['MA5'])}\n"
                f"MA10: {_fmt(latest['MA10'])}\n"
                f"MA20: {_fmt(latest['MA20'])}\n"
                f"MA60 (季線): {ind['MA60']}\n"
                f"RSI(14): {ind['RSI']}\n"
                f"MACD: DIF {ind['DIF']} / DEA {ind['DEA']} / MACD柱 {ind['MACD']}\n"
                f"KD(9,3,3): K {ind['K']} / D {ind['D']}\n"
                f"Bollinger(20,2σ): 上軌 {ind['BB_up']} / 中軌 {ind['BB_mid']} / 下軌 {ind['BB_low']}\n"
                f"3M change: {_fmt(month_change)}%\n"
                f"Last 5 closes: {last5}\n"
            )
        except Exception as e:
            return f"Query failed: {e}"
