"""工具註冊"""
from .tw_stock_tool import (
    TWStockKLineTool,
    TWFundamentalTool,
    TWIndexTool,
    TWMacroTool,
)
from .news_tool import (
    TWStockNewsTool,
    TWMarketNewsTool,
    TWIndustryNewsTool,
    IntlMarketNewsTool,
)
from .stock_resolver import StockResolverTool
from .finmind_tools import (
    FinmindInstitutionalTool,
    FinmindMonthlyRevenueTool,
    FinmindPERTool,
    FinmindMarginTool,
    FinmindPriceTool,
    FinmindShareholdingTool,
    FinmindEPSNetProfitTool,
)
from .news_sentiment_classifier import NewsSentimentClassifierTool
ALL_TOOLS = {
    "StockResolverTool": StockResolverTool(),
    "TWStockKLineTool": TWStockKLineTool(),
    "TWFundamentalTool": TWFundamentalTool(),
    "TWIndexTool": TWIndexTool(),
    "TWMacroTool": TWMacroTool(),
    "TWStockNewsTool": TWStockNewsTool(),
    "TWMarketNewsTool": TWMarketNewsTool(),
    "TWIndustryNewsTool": TWIndustryNewsTool(),
    "IntlMarketNewsTool": IntlMarketNewsTool(),
    "FinmindInstitutionalTool": FinmindInstitutionalTool(),
    "FinmindMonthlyRevenueTool": FinmindMonthlyRevenueTool(),
    "FinmindPERTool": FinmindPERTool(),
    "FinmindMarginTool": FinmindMarginTool(),
    "FinmindPriceTool": FinmindPriceTool(),
    "FinmindShareholdingTool": FinmindShareholdingTool(),
    "FinmindEPSNetProfitTool": FinmindEPSNetProfitTool(),
    "NewsSentimentClassifierTool": NewsSentimentClassifierTool(),
}
