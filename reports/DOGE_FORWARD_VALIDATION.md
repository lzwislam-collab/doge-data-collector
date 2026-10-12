# DOGE 前瞻验证与现货纸面收益

截止 UTC：2026-10-12T17:55:23.766647+00:00

运行状态：ok；90%覆盖率、70%准确率与经济优势均须单独验收。

|期限|正式到期|有效原始报价|报价覆盖率|发行覆盖率|验收|
|---|---:|---:|---:|---:|---|
|1h|12|12|100.0|72.22|COVERAGE_FAILED|
|24h|0|0|None|None|INSUFFICIENT_EVIDENCE|

所有基准使用同一组正式预测和同一报价；缺失仍进入覆盖率分母。
逐模型准确率、配对胜负、首次提交凭据和输入哈希见 JSON。

## 固定成本前瞻现货纸面验证

{
  "completed_intervals": 0,
  "blocked_forecast_id": null,
  "all_selected_intervals_complete": false,
  "strategy_net_return_pct": null,
  "buy_hold_net_return_pct": null,
  "cash_net_return_pct": 0,
  "strategy_checkpoint_max_drawdown_pct": null,
  "intervals": [],
  "economic_edge_validated": false,
  "paper_only_not_executable_fills": true,
  "drawdown_between_checkpoints_unmeasured": true,
  "strategy_rule": "24h first UTC-day forecast; UP long then liquidate, DOWN cash; cash between intervals; stop at first missing or overlapping interval",
  "effective_from_utc": "2026-10-12T00:00:00+00:00",
  "registered_daily_intervals": 1,
  "missing_issuance_days": 0
}

费用每侧0.10%，滑点每侧0.05%；仅研究假设，非实际成交。
出现首次漏评、漏发行或区间重叠即停止累计，已有收益仅代表完整前缀。
持币基准覆盖同一首尾时间；现金0%；区间内回撤尚未测量。

限制：GitHub管理员可改写历史或删除归档；尚无独立外部不可改写存证。
正式样本不足，不声称达标，不交易，不修改定投规则。
