# AGENTS.md

Taiwan (台股) quantitative trading analysis on CrewAI: 5 sequential LLM agents
(market → technical → fundamental → news → chief risk officer) produce Markdown
reports merged into a single PDF. Everything user-facing — docs, agent roles,
generated reports — is Traditional Chinese; keep that.

## Run
- `python main.py`, then type a stock code (TWSE/TPEx, e.g. `2327`). Empty input defaults to `2327`.
- Output: `output_{stock_id}_{YYYYMMDD_HHMMSS}.pdf` in repo root.
- No tests, linter, CI, or git repo. Verification is manual: run `main.py` and inspect the PDF.
- `.env` holds real API keys (`OPENAI_API_KEY`, `FINMIND_TOKEN`), loaded via `load_dotenv()` in `main.py`. Never log or commit them.

## 每次修改後的驗證（必須遵守）
1. **改完檔案一定要執行一次**：任何檔案修改（工具、playbook、crew.json、main.py 等）後，都必須跑 `python main.py` 實測一次，親眼看 PDF 產出與各 agent 報告，確認改動有效、沒有把流程弄壞。不能只做語法檢查就當完成。
2. **能跑不等於正確，資料新鮮度要上網複查**：PDF 產出後，必須上網（websearch/查證資料源）逐一確認 5 個 agent 使用的資料沒有過時——股價/均線/技術指標、EPS/營收/PER、大盤與總經數字、以及新聞標題與日期。若像 8150 那次一樣發現舊資料、過期新聞、或 Yahoo/第三方資料被誤用，必須**直接修改資料源或工具**（例如改用 FinMind 官方財報、加樣本下限、停用過時欄位），修完回到第 1 條再執行一次驗證。

## Wiring (not obvious from filenames)
- `main.py` is the only entrypoint. It reads role/goal/backstory from `agents/*.json` but **ignores** their `llm` and `tools` fields — LLMs and tools are hard-wired in `main.py` (`LLM` dict, `make_agent()`).
- `crew.json` defines the 5 task descriptions + order; `main.py` builds CrewAI `Task`s from it at runtime. Each task description mandates calling `stock_identity_resolver` first — keep that when editing. The chief task (last) gets the other 4 as `context`.
- `skills/*.md` playbooks are injected into backstories via the `SKILLS` dict in `main.py`; the `knowledge`/`skills` lists in `crew.json` are ignored. New playbooks must be added to `SKILLS`.
- `tools/__init__.py` `ALL_TOOLS` is a registry only — `main.py` imports tool classes directly.

## Hard-won fixes (preserve)
- LLMs target a local Ollama server (`http://localhost:11434/v1`), model `minimax-m3:cloud`, API key `ollama`. `main.py` hard-codes `OPENAI_API_BASE`/`OPENAI_API_KEY` (overrides `.env`) and all 5 agents use the same `_ollama_llm()`. Ollama must be running (`ollama serve`) on the Windows host (WSL hits `localhost` correctly since it's not containerized). `TextToolLLM` overrides `supports_function_calling() → False` — reasoning models (incl. `minimax-m3:cloud`) return empty content on native function calls (CrewAI "Invalid response from LLM call"), so all LLM wiring must keep `TextToolLLM` for text-based tool calling.
- Zen free tier is load-limited: under concurrent requests `deepseek-v4-flash-free` frequently returns 500/503 (even a trivial single call can fail when saturated). `_zen_llm` sets `max_retries=5` and `run_crew` retries the whole kickoff up to 4× with backoff. When the API is degraded, expect failures anyway — wait and retry later.
- Tool `_run` methods accept `**kwargs` to swallow stray args the LLM passes — keep this pattern.
- Anti-hallucination rule (in `GOVERNANCE` in `main.py` + playbooks): any tool returning N/A / nan / No data / failure → report says 「資料不足」 and stop. Never fabricate numbers, news headlines, or source names.
- EPS is FinMind-only: Yahoo's `trailingEps`/`forwardEps`/PE/target are disabled in `TWFundamentalTool` (stale for TW mid-caps). EPS comes solely from `finmind_eps_net_profit`; forward EPS is derived from it (run-rate `latest_q_eps×4` or `TTM×(1+YoY)`, take the conservative one) and labeled 「推估值」.
- PER sample floor: `FinmindPERTool` queries 2 years and returns sample count + date span + P25/P50/P75; if < 60 valid samples it returns 「資料不足」 so agents must not compute 3-scenario target prices (a thin sample inflates PE multiples — the root cause of an absurd 220 NTD fair value for 8150).
- News tools scrape Google News RSS (zh-TW); network-dependent. "No news" triggers 資料不足, not retries.

## Gotchas
- `md_to_pdf.py` requires a Windows Chinese font (JhengHei/標楷體) at `C:\Windows\Fonts` or `/mnt/c/Windows/Fonts`, with CSS fallbacks for table-row split failures. Its deps `markdown`, `reportlab`, `xhtml2pdf` (and `pandas` in `tools/finmind_tools.py`) are **not** in `requirements.txt`.
- `md_to_pdf.py` normalizes every `<table>` via `_TableNormalizer` before xhtml2pdf: it drops all-empty columns, pads rows to the header width, and fills empty cells with `&nbsp;`. This is a hard fix — without it, LLM tables with empty leading header cells (e.g. `| | 指標類別 | 數值 | 判讀 |`) or mismatched row widths make xhtml2pdf compute ~6pt phantom micro-columns that stack CJK text char-by-char (old PDFs showed 3 vertical lines at 42.5/48.5/51.5). Don't remove or bypass this pass.
- Long CJK paragraphs (e.g. chief agent 的「裁示理由」) landing at a page bottom can make reportlab raise `LayoutError: Splitting error(n==2)`: xhtml2pdf `PmlParagraph.split` subtracts `deltaHeight` and estimates height as `s*leading`, but the re-wrap in `frame.add` uses actual per-line heights (`autoLeading='max'`, inline `<b>`/`<code>` change metrics), so S[0] doesn't fit and the whole PDF dies. The old fallback CSS kept 9pt so pagination never changed and the error repeated. Fix (hard-won, preserve): fallback_css now steps font down 9pt→8.5→8→7.5pt so the bad paragraph no longer sits at the page seam; as a last resort `_split_long_paragraphs` chops any `<p>` over ~180 chars at CJK punctuation into multiple short paragraphs so no single flowable is too tall to split.
- CPython 3.13 in `venv/`, built with `uv venv venv --python 3.13.14` (Ubuntu 26.04 has no 3.13; `uv` downloads it). Run everything as `venv/bin/python main.py`. `requirements.txt` is insufficient — md_to_pdf deps (`markdown`, `reportlab`, `xhtml2pdf`) are also needed, and **`reportlab` must be pinned `<5`** with `xhtml2pdf==0.2.17` (reportlab 5.0.0 removed `ShowBoundaryValue` and breaks xhtml2pdf's `context.py` import). Full install: `uv pip install --python venv/bin/python -r requirements.txt markdown reportlab xhtml2pdf`, then pin `reportlab<5` + `xhtml2pdf==0.2.17`.
- FinMind free tier ~600 requests/day; all data tools share one `FINMIND_TOKEN`.
- PDF is generated only if all 5 agent reports are collected; otherwise `main.py` warns and lists missing agents.
