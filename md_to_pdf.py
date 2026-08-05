"""
將 5 份 agent Markdown 報告合併轉成單一 PDF。

字型策略：
  - 以 reportlab pdfmetrics 註冊 Windows 中文字型（JhengHei/標楷體），
    再把它對應到 xhtml2pdf 的 DEFAULT_FONT，CSS 用 font-family 指定即可。
  - 避開 @font-face + getNamedFile() 在 WSL 環境複製字型檔到 %TEMP%
    後 reportlab 無法開啟的 PermissionError 問題。
"""
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

import markdown
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logging.getLogger("xhtml2pdf").setLevel(logging.CRITICAL)

from xhtml2pdf import pisa  # noqa: E402
from xhtml2pdf import default as xhtml2pdf_default  # noqa: E402

# 中文字型註冊（第一次 import 即註冊）
_FONT = "JhengHei"
for _candidate in (
    r"C:\Windows\Fonts\msjh.ttc",
    r"C:\Windows\Fonts\kaiu.ttf",
    r"/mnt/c/Windows/Fonts/msjh.ttc",
    r"/mnt/c/Windows/Fonts/kaiu.ttf",
):
    if Path(_candidate).exists():
        try:
            pdfmetrics.registerFont(TTFont(_FONT, _candidate))
            xhtml2pdf_default.DEFAULT_FONT[_FONT.lower()] = _FONT
            xhtml2pdf_default.DEFAULT_FONT[_FONT] = _FONT
            break
        except Exception:
            continue

CSS = """
<style>
@page { size: A4; margin: 1.5cm; }
body { font-family: JhengHei; font-size: 9pt; color: #111; line-height: 1.45; -pdf-word-wrap: CJK; }
h1 { font-size: 14pt; color: #1a3c6e; border-bottom: 2px solid #1a3c6e; padding-bottom: 4px; margin: 4px 0 8px 0; }
h2 { font-size: 12pt; color: #1a3c6e; margin: 12px 0 4px 0; }
h3 { font-size: 10.5pt; color: #333; margin: 10px 0 3px 0; }
p { margin: 3px 0; }
ul, ol { margin: 3px 0 3px 18px; padding: 0; }
li { margin: 2px 0; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; table-layout: auto; }
th { background: #1a3c6e; color: #fff; border: 1px solid #555; padding: 3px 6px; text-align: center; font-size: 8.5pt; -pdf-word-wrap: CJK; }
td { border: 1px solid #999; padding: 3px 6px; font-size: 8.5pt; -pdf-word-wrap: CJK; }
tr { page-break-inside: avoid; }
blockquote { border-left: 3px solid #999; margin: 4px 0 4px 8px; padding: 2px 0 2px 8px; color: #444; }
code { font-family: Courier; background: #f0f0f0; padding: 0 2px; }
hr { border: 0; border-top: 1px solid #ccc; }
.agent-title { font-size: 15pt; font-weight: bold; color: #fff; background: #1a3c6e;
  padding: 6px 10px; margin: 14px 0 8px 0; }
.agent-title.pb { page-break-before: always; }
</style>
"""

AGENT_TITLES = {
    "market_analyst": "一、大盤與總體經濟分析",
    "technical_analyst": "二、技術面分析",
    "fundamental_analyst": "三、基本面分析",
    "news_sentiment_analyst": "四、新聞與消息面分析",
    "chief_risk_officer": "五、風控長最終決策",
}


def _clean(raw: str) -> str:
    raw = re.sub(r"\x1B[@-_][0-?]*[ -/]*[@-~]", "", raw)
    return raw.strip()


# 無字形字元正規化：把 JhengHei 沒有的 emoji/符號映射到可渲染字元，
# 避免 PDF 出現 .notdef 豆腐方塊（如評等星 ⭐ 變成 5 個 □）。
_GLYPH_MAP = {
    "\u2b50": "\u2605",  # ⭐ → ★
    "\U0001f31f": "\u2605",  # 🌟 → ★
    "\u2728": "\u2605",  # ✨ → ★
    "\u2b06": "\u25b2",  # ⬆ → ▲
    "\u2b07": "\u25bc",  # ⬇ → ▼
    "\u25fc": "\u25a0",  # ◼ → ■
    "\u25fb": "\u25a1",  # ◻ → □
    "\u2b1b": "\u25a0",  # ⬛ → ■
    "\u2b1c": "\u25a1",  # ⬜ → □
    "\U0001f7e5": "\u25a0",  # 🟥 → ■
    "\U0001f7e9": "\u25a0",  # 🟩 → ■
    "\U0001f7ea": "\u25a0",  # 🟨 → ■
    "\u26aa": "\u25cb",  # ⚪ → ○
    "\U0001f534": "\u25cf",  # 🔴 → ●
    "\U0001f7e2": "\u25cf",  # 🟢 → ●
    "\U0001f680": "\u25b2",  # 🚀 → ▲
    "\U0001f4c8": "\u25b2",  # 📈 → ▲
    "\U0001f4c9": "\u25bc",  # 📉 → ▼
    "\u2265": ">=",  # ≥ → >=
    "\u2264": "<=",  # ≤ → <=
    "\u200b": "",  # 零寬空格
    "\ufeff": "",  # BOM
}


def _normalize_unsupported_glyphs(text: str) -> str:
    """把 JhengHei 無對應字形的字元剝除或映射，杜絕豆腐方塊。"""
    try:
        has_glyph = pdfmetrics.getFont(_FONT).face.charToGlyph.get
    except Exception:
        has_glyph = None
    out = []
    for ch in text:
        cp = ord(ch)
        if cp < 0x20 or cp == 0x7F:
            out.append(ch)  # 保留換行/縮排等控制字元
            continue
        m = _GLYPH_MAP.get(ch)
        if m is not None:
            out.append(m)
            continue
        if has_glyph is not None and has_glyph(cp):
            out.append(ch)
            continue
        # JhengHei 無字形且無對應映射 → 剝除，避免豆腐
    return "".join(out)


class _TableNormalizer(HTMLParser):
    """正規化 markdown 產生的 <table>，避免 xhtml2pdf 的 phantom 窄欄跑版。

    根因：LLM 偶爾會輸出「表頭含空首格 / 各列欄數不一致」的 markdown 表格，
    xhtml2pdf 在 auto layout 下會把空欄算成 ~6pt 的 phantom 微欄，CJK 文字
    逐字垂直堆疊（舊 PDF 的 42.5/48.5/51.5 三線即為此）。此 class 在送給
    xhtml2pdf 前先：(1) 移除所有列皆為空的欄；(2) 每列補足到表頭欄數；
    (3) 空格填 &nbsp; 防止空欄 collapse。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.in_table = False
        self.rows = []
        self.cur_row = []
        self.cur_cell = None
        self.cur_tag = None

    def handle_starttag(self, tag, attrs):
        if tag == "table" and not self.in_table:
            self.in_table = True
            self.rows = []
            return
        if self.in_table:
            if tag == "tr":
                self.cur_row = []
            elif tag in ("td", "th"):
                self.cur_cell = []
                self.cur_tag = tag
            elif self.cur_cell is not None:
                # 保留 cell 內的 inline 標記（<strong>/<br>/<a>…）
                self.cur_cell.append(self._open_tag(tag, attrs))
            return
        self.out.append(self._open_tag(tag, attrs))

    def handle_startendtag(self, tag, attrs):
        if self.in_table and self.cur_cell is not None:
            self.cur_cell.append(self._open_tag(tag, attrs, self_closing=True))
        elif not self.in_table:
            self.out.append(self._open_tag(tag, attrs, self_closing=True))

    def handle_endtag(self, tag):
        if self.in_table:
            if tag in ("td", "th"):
                self.cur_row.append((self.cur_tag, "".join(self.cur_cell)))
                self.cur_cell = None
            elif tag == "tr":
                if self.cur_row:
                    self.rows.append(self.cur_row)
                self.cur_row = []
            elif tag == "table":
                self.out.append(self._render_table(self.rows))
                self.in_table = False
            elif self.cur_cell is not None:
                self.cur_cell.append(f"</{tag}>")
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.in_table and self.cur_cell is not None:
            self.cur_cell.append(data)
        elif not self.in_table:
            self.out.append(data)

    @staticmethod
    def _open_tag(tag, attrs, self_closing=False):
        if not attrs:
            return f"<{tag}{'/' if self_closing else ''}>"
        a = " ".join(f'{k}="{v}"' for k, v in attrs)
        return f"<{tag} {a}{'/' if self_closing else ''}>"

    def _render_table(self, rows):
        if not rows:
            return ""
        ncols = max(len(r) for r in rows)
        empty_cols = {
            c for c in range(ncols)
            if all(c >= len(r) or not r[c][1].strip() for r in rows)
        }
        out = ["<table>"]
        for r in rows:
            cells = r + [("td", "")] * (ncols - len(r))
            cells = [c for i, c in enumerate(cells) if i not in empty_cols]
            # 空格填 &nbsp;，避免 xhtml2pdf 把空欄算成 ~6pt phantom 窄欄
            cells = [
                (t, "&nbsp;" if not v.strip() else v) for t, v in cells
            ]
            out.append("<tr>" + "".join(f"<{t}>{v}</{t}>" for t, v in cells) + "</tr>")
        out.append("</table>")
        return "".join(out)


def _normalize_tables(html: str) -> str:
    p = _TableNormalizer()
    p.feed(html)
    p.close()
    if p.in_table:
        p.out.append(p._render_table(p.rows))
    return "".join(p.out)


def md_to_pdf(reports: dict, output_path: str) -> str:
    """reports: {agent_key: markdown_text}，依 AGENT_TITLES 順序輸出"""
    body = []
    for idx, key in enumerate(AGENT_TITLES):
        text = reports.get(key)
        if not text:
            continue
        cls = "agent-title" + (" pb" if idx > 0 else "")
        body.append(f'<div class="{cls}">{AGENT_TITLES[key]}</div>')
        md_html = markdown.markdown(
            _normalize_unsupported_glyphs(_clean(text)),
            extensions=["tables", "fenced_code", "sane_lists"],
        )
        body.append(_normalize_tables(md_html))

    if not body:
        raise ValueError("沒有可輸出的報告")

    html = f"<html><head><meta charset='utf-8'>{CSS}</head><body>{''.join(body)}</body></html>"

    # 依序嘗試：越後面越保守，處理 reportlab 對「過高表格列」的拆分失敗，
    # 以及長 CJK 段落落在頁尾時 Paragraph.split 估高不足造成的
    # "Splitting error(n==2)"（根因：xhtml2pdf PmlParagraph split 與 re-wrap
    # 對 deltaWidth/deltaHeight/實際行高 的估算不一致）。
    # 前三級：縮小字級→改變分頁點，讓同一段落不再卡在頁尾無法拆分；
    # 最後一級：直接在 HTML 層把過長 <p> 切短，從源頭避免「單段過高」。
    fallback_css = [
        None,  # 原始 CSS
        """
<style>
body { font-family: JhengHei; font-size: 8.5pt; color: #111; line-height: 1.4; -pdf-word-wrap: CJK; }
h1 { font-size: 13pt; color: #1a3c6e; }
h2 { font-size: 11pt; color: #1a3c6e; }
p { margin: 3px 0; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; }
th { background: #1a3c6e; color: #fff; border: 1px solid #555; padding: 3px 6px; text-align: center; font-size: 8pt; -pdf-word-wrap: CJK; }
td { border: 1px solid #999; padding: 3px 6px; font-size: 8pt; -pdf-word-wrap: CJK; }
blockquote { border-left: 3px solid #999; margin: 4px 0 4px 8px; padding: 2px 0 2px 8px; }
</style>
""",
        """
<style>
body { font-family: JhengHei; font-size: 8pt; color: #111; line-height: 1.35; -pdf-word-wrap: CJK; }
h1 { font-size: 12pt; color: #1a3c6e; }
h2 { font-size: 10pt; color: #1a3c6e; }
p { margin: 2px 0; }
table { border-collapse: collapse; width: 100%; margin: 5px 0; }
th { background: #1a3c6e; color: #fff; border: 1px solid #555; padding: 2px 5px; text-align: center; font-size: 7.5pt; -pdf-word-wrap: CJK; }
td { border: 1px solid #999; padding: 2px 5px; font-size: 7.5pt; -pdf-word-wrap: CJK; }
blockquote { border-left: 3px solid #999; margin: 3px 0 3px 8px; padding: 2px 0 2px 8px; }
</style>
""",
        """
<style>
body { font-family: JhengHei; font-size: 7.5pt; color: #111; line-height: 1.3; -pdf-word-wrap: CJK; }
h1 { font-size: 11pt; color: #1a3c6e; }
h2 { font-size: 9.5pt; color: #1a3c6e; }
p { margin: 2px 0; }
table { border-collapse: collapse; width: 100%; margin: 4px 0; }
th { background: #1a3c6e; color: #fff; border: 1px solid #555; padding: 2px 4px; text-align: center; font-size: 7pt; -pdf-word-wrap: CJK; }
td { border: 1px solid #999; padding: 2px 4px; font-size: 7pt; -pdf-word-wrap: CJK; }
blockquote { border-left: 3px solid #999; margin: 3px 0 3px 8px; padding: 2px 0 2px 8px; }
</style>
""",
    ]

    out = Path(output_path)
    last_err = None
    for variant in fallback_css:
        doc_html = html if variant is None else html.replace(CSS, variant, 1)
        try:
            with open(out, "wb") as f:
                status = pisa.CreatePDF(doc_html, dest=f, encoding="utf-8")
            if status.err:
                last_err = f"status.err={status.err}"
                continue
            return str(out)
        except Exception as e:
            last_err = str(e)
            continue

    # 最後手段：把過長 <p> 拆成多個短段，杜絕「單段過高→無法拆分」
    body = []
    for idx, key in enumerate(AGENT_TITLES):
        text = reports.get(key)
        if not text:
            continue
        cls = "agent-title" + (" pb" if idx > 0 else "")
        body.append(f'<div class="{cls}">{AGENT_TITLES[key]}</div>')
        md_html = markdown.markdown(
            _normalize_unsupported_glyphs(_clean(text)),
            extensions=["tables", "fenced_code", "sane_lists"],
        )
        body.append(_normalize_tables(_split_long_paragraphs(md_html)))

    if body:
        html2 = f"<html><head><meta charset='utf-8'>{fallback_css[3]}</head><body>{''.join(body)}</body></html>"
        try:
            with open(out, "wb") as f:
                status = pisa.CreatePDF(html2, dest=f, encoding="utf-8")
            if not status.err:
                return str(out)
            last_err = f"status.err={status.err}"
        except Exception as e:
            last_err = str(e)

    raise RuntimeError(f"PDF 產生失敗（所有排版方案皆失敗）: {last_err}")


def _split_long_paragraphs(html: str, max_len: int = 180) -> str:
    """把單個 <p> 內過長內容拆成多個 <p>，避免段落高過整頁而無法拆分。"""
    import re as _re

    def repl(m):
        inner = m.group(1)
        # 保留行內標籤（<strong>/<br> 等），只切純文字長度
        parts = []
        cur = []
        for tok in _re.split(r"(<[^>]+>)", inner):
            if not tok:
                continue
            if tok.startswith("<"):
                cur.append(tok)
                continue
            while len(tok) > max_len:
                cut = max_len
                # 優先在標點/空白切，避免切開數字
                for sep in ("。", "；", "；", "，", " ", "：", "、", "！", "？"):
                    idx = tok.rfind(sep, 0, cut)
                    if idx > max_len // 2:
                        cut = idx + 1
                        break
                cur.append(tok[:cut])
                parts.append("".join(cur))
                cur = []
                tok = tok[cut:]
            if tok:
                cur.append(tok)
        parts.append("".join(cur))
        kept = [p for p in parts if p.strip()]
        if len(kept) <= 1:
            return "<p>" + inner + "</p>"
        return "</p>\n<p>".join("<p>" + p for p in kept) + "</p>"

    return _re.sub(r"<p>(.*?)</p>", repl, html, flags=_re.S)
