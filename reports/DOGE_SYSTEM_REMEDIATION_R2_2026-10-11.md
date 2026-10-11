# DOGE系统全面修订｜R2验收记录（2026-10-11）

**裁决：数据与报告工程 PASS/PARTIAL；70%极端暴跌预测目标 BLOCKED；预测优势 NO_VALIDATED_EDGE。**

## 本次已经正式修改并实测
1. **①–⑤采集保留**：OKX日线、小时研究、链上、巨鲸、ETF、宏观及政策采集器不改采集含义；过期或局部采样不能冒充完整数据。原36项及11章V4.1研究报告保留。
2. **真实未来小时预警**：新增 `crash_forward_watch.py`、`test_crash_forward_watch.py`。严格只取运行时新近已收盘1H K线，记录真实issued_at与source_first_ingested，缺历史记录不得事后回填。预警规则在看到2026-10-09暴跌后选定，不得据此反向宣称历史预测成功。规则版本由Git提交`4477ca2197d5dbf185e8fc26cab9fbcb61fe4c2d`指认；历史第一条有效登记时间2026-10-11T02:12:54.630560Z，不倒填冻结时间。
3. **每小时写入/评分**：`.github/workflows/okx_hourly_extreme_history.yml`改为UTC每小时13分采集，03/15时段允许较大批次历史回填，其余时段仅请求最近历史；按注册时点到期12个确认小时后才评分，清楚标出未观察窗口、漏报和误报；失败仍保存诊断，不将失败标成PASS。
4. **真实首次运行**：Actions `38104413729`和`38104766008`均执行成功，已在`data/crash_research/prospective_hourly.csv`回读首条有效hourly checkpoint；`prospective_scores.json`登记1条、已评分0。首次规则未触发，不得宣称盈利或70%准确率。
5. **历史暴跌研究真实负结果**：`evaluate_crash_warning.py`追加误报每周和提前量，不调整事后规则阈值。最新回填研究样本至少7300小时，每小时DOGE/BTC/ETH，目标单小时跌幅≤-5%共3次；事后规则触发24次，真正提前对应1次，precision约4.17%，recall 33.33%，误报约0.531次/周，唯一命中事件提前10小时；**明确未达70%，事件稀少且事后选参，未做独立前瞻验证**。
6. **数据质量/新鲜度**：`feed_freshness_audit.py`区分旧巨鲸SSE（可选且覆盖有限）与确认区块扫描（主要链上大额跟踪）；OKX/Binance/Bybit强平覆盖限制分列；质量结果写入`data/quality/latest.json`；独立合成测试和真实工作流 `38104471369`成功。旧SSE过期不再掩盖新区块采集状态，但新区块活跃也不意味着交易所净流入已核验。
7. **V4.1预测时点**：`forecast_v41.py`、`score_v41.py`和`report_v41.py`明确UTC日线收盘到目标UTC日线收盘，与报告发布后完整24小时不同；显示原始有效预测首次登记时点和报告生成时点，保留已有登记及模型版本，不追溯修改。
8. **旧模型时效**：报告显式对比最新DOGE日线与旧概率基线截止时间，过期标`STALE_NOT_USED_FOR_V41`；旧模型门禁`status=ok`不能当作预测优势。
9. **V4.1报告**：F09单独披露历史暴跌研究失败/实际未来登记，F11披露源级质量及Google Sheets/Apps Script独立验收限制。11章仍全部存在，Actions `38104718921`和报告测试`38104718850`成功，报告已回读。没有修改DOGE定投/Account OS授权。

## 未通过／不能靠改代码立即解决
- 70%暴跌预警（要求precision及recall都≥70%，还需独立事件数量、误报预算、真实注册、窗口长度与提前时间）：**BLOCKED**。历史回测仅3次目标事件，未来已成熟前瞻样本0。
- OKX历史小时数据仍是部分回填；真正完整2023年起的数据、超过30例**独立**目标事件及严格未使用的验证数据未达到。
- 预测发行到实际日线目标窗口之间存在约1–2小时延迟。当前明确披露，不将其称为从发行起完整24小时预测；如需精确滚动24小时，则未来以正式注册时点价格重新建立独立模型与回测，不可悄然改变旧记录。
- 交易所真实净流入（实体地址标签/所有权未核实）、全球爆仓覆盖、情绪与政策事件因果效应：**PARTIAL**。
- GitHub`push`成功已验证，但**更新的每小时/每日cron长期稳定性仍要等待真实schedule事件+对应数据回读**，不能由推送运行证明。
- Google Sheets任务状态表中Apps Script最近确认的日采集运行仍止于2026-10-06，**未独立核对其之后的触发器、执行及实际Sheet写入**；不得宣称已验证持续运行，不新增定时触发器，不修改OKX_Daily_Collector_v1.2。
- Git存档不断增长，长期需分片及定期不可变检查；尚未迁移，不得称性能优化已完成。

## 测试证据
- 前瞻小时扫描及登记：`https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38104766008`
- 单独新鲜度审计：`https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38104471369`
- V4.1生产链及预测时点报告：`https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38104718921`
- V4.1章节报告测试：`https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38104718850`

**下一个验收动作**：当UTC目标12小时小时K线已经真实采集，复核首条前瞻评分；并在未来真正schedule运行时核对登记、写入和采样时延。历史策略不因这次研发而自动升级或交易。
