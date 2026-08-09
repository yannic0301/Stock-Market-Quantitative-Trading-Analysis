"""
台股量化交易 Crew - 真正最終版（Zen 免費模型版）
"""
import os
import sys
import io
import json
import time
import logging
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

from dotenv import load_dotenv
load_dotenv()

# Zen API 設定（使用 .env 的 OPENAI_API_KEY，base_url 指向 Zen）
os.environ["OPENAI_API_BASE"] = os.environ.get("OPENAI_API_BASE", "https://opencode.ai/zen/v1")
# OPENAI_API_KEY 從 .env 讀取（需為 Zen API key）

from crewai import Agent, Task, Crew, Process
from crewai import LLM as CrewLLM

from tools.tw_stock_tool import TWStockKLineTool, TWFundamentalTool, TWIndexTool, TWMacroTool
from tools.stock_resolver import StockResolverTool
from tools.news_tool import (
    TWStockNewsTool, TWMarketNewsTool,
    TWIndustryNewsTool, IntlMarketNewsTool,
)
from tools.web_research_tool import WebPageReaderTool
from tools.finmind_tools import (
    FinmindPERTool, FinmindMonthlyRevenueTool,
    FinmindInstitutionalTool, FinmindMarginTool,
    FinmindEPSNetProfitTool,
)

from md_to_pdf import md_to_pdf

# ================ LLM 分工（Zen 免費模型三種搭配） ================
class TextToolLLM(CrewLLM):
    """覆寫 supports_function_calling → 強制使用傳統文字式 tool calling。

    Zen 免費模型在原生 function calling 時可能回傳 content=None/空字串，
    導致 CrewAI 報 "Invalid response from LLM call - None or empty"。
    讓 CrewAI 改用 ReAct 文字式工具呼叫可完全避開此問題。
    """

    def supports_function_calling(self) -> bool:
        return False


def _zen_llm(model: str) -> CrewLLM:
    """建立指向 Zen OpenAI 相容端點的 LLM"""
    llm = TextToolLLM(
        model=model,
        base_url=os.environ.get("OPENAI_API_BASE", "https://opencode.ai/zen/v1"),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )
    llm.max_retries = 5  # Zen 免費額度易觸發 500/503，提高 SDK 重試次數
    return llm


LLM = {
    "market":      _zen_llm("openai/big-pickle"),
    "technical":   _zen_llm("openai/deepseek-v4-flash-free"),
    "fundamental": _zen_llm("openai/nemotron-3-ultra-free"),
    "news":        _zen_llm("openai/deepseek-v4-flash-free"),
    "chief":       _zen_llm("openai/deepseek-v4-flash-free"),
}


def load_agent_config(filename: str) -> dict:
    with open(Path("agents") / filename, encoding="utf-8") as f:
        return json.load(f)


GOVERNANCE = """

【分析指引】
1. 公司名稱優先使用 stock_identity_resolver 工具回傳的 Chinese Name
2. 數字首選引用工具回傳值；若工具回傳 N/A 或 nan，標示「資料不足」
3. 報告結尾必須包含：依據、結論、風險三段
4. 新聞類：如果搜尋結果 < 6 則，明確標示「新聞資料蒐集不完整」，嚴禁生成虛構新聞
5. 【防幻想鐵律】任何工具呼叫失敗、回傳 No data/No news/nan/N/A，一律標示「資料不足」並停止推論。嚴禁：
   a) 用佔位符填補（X.X%、??、?、待補充、示意、例如等）
   b) 虛構任何新聞標題、來源、日期、摘要（尤其嚴禁編造 CNBC/Bloomberg 等媒體）
   c) 編造未經工具回傳的公司名稱、股價、EPS、PER、營收等數字
"""

# 每個 agent 對應的工作守則（playbook），直接注入 backstory 確保 SOP 生效
SKILLS = {
    "market": "skills/technical_analysis_playbook.md",
    "technical": "skills/technical_analysis_playbook.md",
    "fundamental": "skills/fundamental_analysis_playbook.md",
    "news": None,
    "chief": "skills/risk_management_playbook.md",
}


def make_agent(name: str, config_file: str, llm_key: str, tools: list) -> Agent:
    cfg = load_agent_config(config_file)
    extra = ""
    skill_file = SKILLS.get(name)
    if skill_file:
        with open(Path(skill_file), encoding="utf-8") as f:
            extra = "\n\n【工作守則 SOP】\n" + f.read().strip()
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"] + GOVERNANCE + extra,
        llm=LLM[llm_key],
        tools=tools,
        verbose=False,
        allow_delegation=False,
    )


print("🤖 建立 5 個 agents...")

market_agent = make_agent(
    "market", "market_analyst.json", "market",
    [StockResolverTool(), TWIndexTool(), TWMacroTool()]
)

technical_agent = make_agent(
    "technical", "technical_analyst.json", "technical",
    [StockResolverTool(), TWStockKLineTool()]
)

fundamental_agent = make_agent(
    "fundamental", "fundamental_analyst.json", "fundamental",
    [StockResolverTool(), TWFundamentalTool(),
     FinmindPERTool(), FinmindMonthlyRevenueTool(), FinmindInstitutionalTool(),
     FinmindEPSNetProfitTool()]
)

news_agent = make_agent(
    "news", "news_sentiment_analyst.json", "news",
    [StockResolverTool(), TWStockNewsTool(), TWMarketNewsTool(),
     TWIndustryNewsTool(), IntlMarketNewsTool(), WebPageReaderTool()]
)

chief_agent = make_agent(
    "chief", "chief_risk_officer.json", "chief",
    [StockResolverTool(), FinmindMarginTool(), TWIndexTool()]
)

print("✅ 5 個 agents 就緒\n")


def run_crew(stock_id: str):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = f"output_{stock_id}_{timestamp}.pdf"

    print(f"🚀 啟動分析: {stock_id}")
    print(f"📁 PDF: {pdf_path}\n" + "=" * 50)

    with open("crew.json", encoding="utf-8") as f:
        crew_cfg = json.load(f)

    agent_map = {
        "market_analyst": market_agent,
        "technical_analyst": technical_agent,
        "fundamental_analyst": fundamental_agent,
        "news_sentiment_analyst": news_agent,
        "chief_risk_officer": chief_agent,
    }

    tasks = []
    for t in crew_cfg["tasks"]:
        desc = f"【股票代碼】{stock_id}\n\n" + t["description"]
        spec = Task(
            description=desc,
            expected_output=t.get("expected_output", "Markdown 報告"),
            agent=agent_map[t["agent"]],
        )
        tasks.append(spec)

    if len(tasks) >= 5:
        tasks[-1].context = tasks[:4]

    crew = Crew(
        agents=list(agent_map.values()),
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
    )

    result = None
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            result = crew.kickoff(inputs={"stock_id": stock_id})
            break
        except Exception as e:
            print(
                f"\n⚠️ 第 {attempt}/{max_attempts} 次執行失敗"
                f"（可能 Ollama 服務未啟動/推理模型限流或伺服器 500）: {type(e).__name__}: {e}"
            )
            if attempt == max_attempts:
                raise
            time.sleep(20 * attempt)

    print(f"\n{'=' * 50}")
    print("📊 最終決策")
    print("=" * 50)
    print(result)

    # 收集各 agent 報告 → 合併成一份 PDF
    task_outputs = {}
    for t in tasks:
        if t.agent and hasattr(t, "output") and t.output and t.output.raw:
            key = next(
                (k for k, v in agent_map.items() if v is t.agent),
                None,
            )
            if key:
                task_outputs[key] = t.output.raw

    if len(task_outputs) < len(agent_map):
        print(f"\n⚠️ 警告：僅收集到 {len(task_outputs)}/{len(agent_map)} 份報告")
        for k, a in agent_map.items():
            if k not in task_outputs:
                print(f"   - 缺 {k} 報告")
    else:
        out = md_to_pdf(task_outputs, pdf_path)
        print(f"\n✅ PDF 已產生: {out}")
    return result


if __name__ == "__main__":
    stock_id = input("\n請輸入股票代碼: ").strip()
    if not stock_id:
        stock_id = "2327"
    run_crew(stock_id)
