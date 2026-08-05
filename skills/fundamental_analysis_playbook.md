# 基本面分析師工作守則

## 三情境本益比 SOP

拿到 EPS 預估與歷史 PER 後，依下列流程計算三情境合理股價：

### 樂觀情境
- 採用 finmind_pe_ratio_history 回傳之近 2 年「P75」百分位（工具已直接提供）
- 若無歷史，採用同業平均 × 1.2 倍
- 合理股價 = 樂觀 EPS × 樂觀 PER

### 中立情境
- 採用 finmind_pe_ratio_history 回傳之近 2 年「P50」百分位
- 若無歷史，採用同業平均
- 合理股價 = 中立 EPS × 中立 PER

### 悲觀情境
- 採用 finmind_pe_ratio_history 回傳之近 2 年「P25」百分位
- 若無歷史，採用同業平均 × 0.8 倍
- 合理股價 = 悲觀 EPS × 悲觀 PER

## 重要鐵律
1. **絕對禁止自己掰 PER 倍數**，必須有 finmind_pe_ratio_history 工具的數據
2. **若工具回傳 N/A 或「資料不足」**，明確標示「資料不足」並停止推論
3. **PER 樣本不足禁令**：finmind_pe_ratio_history 回傳「資料不足」、有效樣本 < 60 筆、或涵蓋不足 6 個月時，三情境合理股價一律標示「資料不足」，嚴禁以少量樣本套用百分位 PER 推算目標價（樣本不足會把倍數推爆，產生離譜目標價）
4. **PER 失真檢查**：以下任一情況代表 PER 百分位法失真，此時改用「Forward PE 常態倍數（如 15~20 倍）× 推估 EPS」做 sanity check，以 sanity range 為主要結論，不得沿用失真的百分位倍數；並在報告中註明失真原因：
   - PER 中位數（P50）明顯離群（例如 55~88 倍而該股/同業常態僅 10~25 倍）
   - **分布極度右偏**：P75 遠高於 P50（如 P75 > 3×P50，例如 P50 17 / P75 99），通常發生於 TTM EPS 崩跌期間，近期 PER 暴漲不代表未來獲利能力
   - TTM EPS 異常低（轉虧為盈初期、基期極低），任何 PE 倍數都會被放大失真
5. 必須用 finmind_monthly_revenue 看近 12 個月營收趨勢
6. 必須用 finmind_institutional_investors 看法人是否站在買方

## EPS 資料來源優先順序（重要）
1. **實際 TTM EPS 以 finmind_eps_net_profit 為準**：直接用其回傳的「實際 TTM EPS（近四季合計）」，這是官方財報實際值。
2. **Yahoo 的 trailingEps/forwardEps 已停用**：tw_fundamental_query 不再回傳 EPS/PE（Yahoo 對台股財務資料常為過時值），不得引用任何 Yahoo EPS 數字。
3. Forward EPS 推估（一律依 FinMind 財報動能推算）：
   - 方法 A：最新一季 EPS × 4（run-rate）
   - 方法 B：實際 TTM × (1 + 最新一季 YoY 成長率)
   - 兩種方法結果取較保守者，並明確標示「推估值（依 FinMind 財報動能推算）」
4. 三情境 EPS：樂觀=推估值上調、中立=推估值、悲觀=推估值下調，都要標明來源與計算依據，禁止無中生有。
