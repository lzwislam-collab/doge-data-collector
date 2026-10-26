# DOGE 前瞻验证与现货纸面收益

截止 UTC：2026-10-26T23:46:09.455519+00:00

运行状态：ok；90%覆盖率、70%准确率与经济优势均须单独验收。

|期限|正式到期|有效原始报价|报价覆盖率|发行覆盖率|验收|
|---|---:|---:|---:|---:|---|
|1h|271|210|77.49077490774907|75.56|COVERAGE_FAILED|
|24h|14|10|71.42857142857143|100.0|COVERAGE_FAILED|

所有基准使用同一组正式预测和同一报价；缺失仍进入覆盖率分母。
逐模型准确率、配对胜负、首次提交凭据和输入哈希见 JSON。

## 固定成本前瞻现货纸面验证

{
  "completed_intervals": 5,
  "blocked_forecast_id": "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792191600000_24h",
  "all_selected_intervals_complete": false,
  "strategy_net_return_pct": 0.25224953005784645,
  "buy_hold_net_return_pct": -7.21425911709801,
  "cash_net_return_pct": 0,
  "strategy_checkpoint_max_drawdown_pct": 2.0532190585122123,
  "intervals": [
    {
      "forecast_id": "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1791759600000_24h",
      "entry_utc": "2026-10-12T00:01:25.696436+00:00",
      "exit_utc": "2026-10-13T00:01:52.766000+00:00"
    },
    {
      "forecast_id": "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1791846000000_24h",
      "entry_utc": "2026-10-13T00:01:59.236458+00:00",
      "exit_utc": "2026-10-14T00:05:43.664000+00:00"
    },
    {
      "forecast_id": "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1791932400000_24h",
      "entry_utc": "2026-10-14T00:05:50.458617+00:00",
      "exit_utc": "2026-10-15T00:10:39.574000+00:00"
    },
    {
      "forecast_id": "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792018800000_24h",
      "entry_utc": "2026-10-15T00:10:46.805652+00:00",
      "exit_utc": "2026-10-16T00:34:17.568000+00:00"
    },
    {
      "forecast_id": "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792105200000_24h",
      "entry_utc": "2026-10-16T00:34:27.425908+00:00",
      "exit_utc": "2026-10-17T00:36:46.962000+00:00"
    }
  ],
  "economic_edge_validated": false,
  "paper_only_not_executable_fills": true,
  "drawdown_between_checkpoints_unmeasured": true,
  "strategy_rule": "24h first UTC-day forecast; UP long then liquidate, DOWN cash; cash between intervals; stop at first missing or overlapping interval",
  "effective_from_utc": "2026-10-12T00:00:00+00:00",
  "registered_daily_intervals": 15,
  "missing_issuance_days": 0
}

费用每侧0.10%，滑点每侧0.05%；仅研究假设，非实际成交。
出现首次漏评、漏发行或区间重叠即停止累计，已有收益仅代表完整前缀。
持币基准覆盖同一首尾时间；现金0%；区间内回撤尚未测量。

限制：GitHub管理员可改写历史或删除归档；尚无独立外部不可改写存证。
正式样本不足，不声称达标，不交易，不修改定投规则。
