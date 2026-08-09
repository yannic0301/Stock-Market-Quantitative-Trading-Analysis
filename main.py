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

os.environ["OPENAI_API_BASE"] = os.environ.get("OPENAI_API_BASE", "https://opencode.ai/zen/v1")

from crewai import Agent, Task, Crew, Process
from crewai import LLM as CrewLLM

from tools.tw_stock_tool import TWStockKLineTool, TWFundamentalTool, TWIndexTool, TWMacroTool
from tools.stock_resolver import StockResolverTool
from tools.news_tool import (
    TWStockNewsTool, TWMarketNewsTool,
    TWIndustryNewsTool, IntlMarketNewsTool,
)
from tools.web_research_tool import WebPageReaderTool, DeepWebResearchTool
from tools.finmind_tools import (
    FinmindPERTool, FinmindMonthlyRevenueTool,
    FinmindInstitutionalTool, FinmindMarginTool,
    FinmindEPSNetProfitTool,
)

from md_to_pdf import md_to_pdf

class TextToolLLM(CrewLLM):
    """Force traditional text-based tool calling for the Zen free models."""
    def supports_function_calling(self) -> bool:
        return False


def _zen_llm(model: str) -> CrewLLM:
    llm = TextToolLLM(
        model=model,
        base_url=os.environ.get("OPENAI_API_BASE", "https://opencode.ai/zen/v1"),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )
    llm.max_retries = 5
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
6. 【Web Research】使用 research dossier 時，只有文章正文明確出現的資訊才可視為證據；無法讀取原文的新聞只能引用搜尋結果明確提供的標題、來源、日期與 URL。
"""

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
    [
        StockResolverTool(),
        TWStockNewsTool(), TWMarketNewsTool(),
        TWIndustryNewsTool(), IntlMarketNewsTool(),
        WebPageReaderTool(), DeepWebResearchTool(),
    ]
)

chief_agent = make_agent(
    "chief", "chief_risk_officer.json", "chief",
    [StockResolverTool(), FinmindMarginTool(), TWIndexTool()]
)

print("✅ 5 個 agents 就緒\n")


NEWS_RESEARCH_TASK = """
【Phase 2 Web Research - 必做】
1. 先呼叫 stock_identity_resolver({stock_id})，確認公司中文名稱與產業。
2. 優先呼叫 deep_web_research，輸入 company=確認後的公司中文名稱、industry=確認後的產業、focus=「近期可能影響股價、基本面、估值或市場風險的事件」。
3. deep_web_research 會自動執行多組獨立搜尋、去重、來源/類別多樣化選擇，並嘗試閱讀多篇原文。這是本任務的主要研究工具，不要只做固定 9 則標題蒐集。
4. 如果 dossier 的原文閱讀不足、某個關鍵事件需要驗證，再使用既有 tw_stock_news_search、tw_market_news_search、tw_industry_news_search、international_market_news_search 或 web_page_reader 補查。
5. 報告必須把資訊分成「已驗證原文證據」「只有搜尋標題的線索」「你的推論」。只有 Article text 中明確出現的內容才算已驗證證據。
6. 優先尋找公司官方公告/法說會/政府或交易所資料，再使用原始財經媒體交叉驗證。若不同來源互相矛盾，明確列出矛盾，不得自行編一個答案。
7. 不得編造任何新聞、日期、來源、數字、摘要或文章內容。讀不到原文就寫「資料不足」，不得假裝讀過。
8. 最終判斷市場情緒與風險時，必須說明每個重要結論是由哪些已驗證事件支持。
"""


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
        if t["agent"] == "news_sentiment_analyst":
            desc = f"【股票代碼】{stock_id}\n\n" + NEWS_RESEARCH_TASK
        else:
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
                f"（可能 Zen 免費模型限流或伺服器 500）: {type(e).__name__}: {e}"
            )
            if attempt == max_attempts:
                raise
            time.sleep(20 * attempt)

    print(f"\n{'=' * 50}")
    print("📊 最終決策")
    print("=" * 50)
    print(result)

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
        for k in agent_map:
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
