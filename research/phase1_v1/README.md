# DOGE 第一阶段研究入口

结论：交付独立可复现研究，不授权正式模型晋升。旧生产实现、数据及前瞻协议以 `frozen_assets.json` 的固定 Git 提交和2670项 SHA256 为保护边界。

## 重现

使用 Python 3.11、NumPy 2.3.5；BLAS线程固定为1。先导出新空目录，再研究；禁止直接把持续更新的 main 数据当成同一输入。

```text
python -m pip install numpy==2.3.5
python -m unittest discover -q
python forward_validation_v2.py --integrity-only
python phase1_research_snapshot.py --out ../phase1-input-new
python phase1_research.py --source ../phase1-input-new --out ../phase1-results-new
```

Windows 可设置 `PYTHONUTF8=1`、`OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`。冻结清单采用 Git归档的原始字节；本地自动换行转换不代表远端资产改变。输出目录不得包含任何旧资产。导出目录已存在且非空时明确拒绝，不覆盖。

## 实际实现

- 4组9个因子，full、逐组删除、单组共9个配置；1H/24H各6折，共108个拟合模型。
- 固定4096小时训练、512小时验证、512小时测试，24小时 embargo，并额外 purge 对应预测期限。验证指标仅记录，参数不因验证/测试成绩改变。
- 标准化和L2 logistic仅拟合训练窗口，零初始化、固定120步，因此不依赖随机抽样。配置中的seed是未来实验扩展的预留固定标识，本版本没有随机训练。特征、拟合参数、预测概率固定12位小数；评分使用保存后的概率，产物采用同一数值合同，避免BLAS机器精度差异导致同身份不同字节。
- `receipt_time` 模式同时检查特征与目标标签实际入库时间；不足时WAIT，绝不倒填历史可用时间。默认模式是明确标注的事后收盘研究，非真实前瞻证据。
- 每事件保存候选概率、冻结反转规则方向、majority/BTC基准及目标；恒涨/恒跌用同一事件集合。冻结规则没有校准概率，`frozen_p=null`，不编造概率成绩。
- 全部测试事件留存；非重叠分析按固定首相位留完整目标加1小时间隔；仍不假定统计独立。输出事前24小时UP/DOWN分层、配对胜负、准确率、balanced accuracy、Brier和消融差，全部为描述性。
- `candidates/` 登记实验及9个配置的逐折模型哈希，不覆盖已有身份。代码、输入、政策、运行环境、冻结清单与结果绑定。精确重放必须保持输入/环境；浮点平台差异须核实，不冒充相同结果。

## 36项治理与下一阶段

36项回读为14项治理/基础、10项研究、5项监测、7项停止旧评分资格。治理条目不是方向因子；停止评分资格不等于已经证明负增益或删除历史。首批价格组是治理中基础行情的研究衍生；其余链上、巨鲸、宏观、事件、OI/Funding等需要真实PIT适配器和公开见证，不能虚构历史特征。

既有 `factor_evaluation_evidence_v1.py` 检查真实原始/公开证据，复用其冻结未来协议及480项家族校正、固定复核日期、配对/行情/成本门槛，仅适用于其原先注册的4组。这里新训练的模型尚未被该协议注册；不得直接扩充原家族或倒用旧成绩晋升。

新候选先建立**新的未来生效协议**，锁定完整比较家族、复核日、效应和退场边界、块长度与停止规则；模型与预测在发行前公开注册、所有预约机会保留分母、至少100个非重叠样本和90天且跨行情充分覆盖。之后在同事件冻结模型/基准上实施依赖稳健推断及多重比较，接入 `p2_economic_acceptance.py` 的bid/ask/深度/费用/滑点和压力成本证据，接入 `stage5_error_review.py` 的错误复盘。只有独立评价及人工版本审批后才可晋升/退出；数据PIT失败先隔离。

70%须长期真实前瞻、依赖稳健统计下界和跨行情稳定性、超越基准共同支持。本次历史研究不产生该声明。没有下单入口。无新增cron或Apps Script触发器。Git不是外部不可改写存储；Google Sheets现表和独立WORM尚未核实。

## 官方方法来源（2026-10-13查阅）

- [FreqAI运行/滚动回测与预测缓存](https://www.freqtrade.io/en/stable/freqai-running/)：分训练窗口和回测窗口，特征必须防前视。
- [Qlib Recorder](https://qlib.readthedocs.io/en/stable/component/recorder.html)、[官方 rolling 示例](https://github.com/microsoft/qlib/blob/main/examples/model_rolling/task_manager_rolling.py)：记录参数、产物及滚动实验身份。
- [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent)、[RD-Agent-Quant论文](https://arxiv.org/abs/2505.15155)：借鉴因子实现、评价反馈及消融循环；其股票研究收益不等于DOGE方向准确率。

## 验证状态

测试计数以最终交付审计为准。独立审查发现receipt标签迟到漏洞，已补测修复；增加数值合同检查。首次Windows运行出现编码及自动换行问题，已定位为环境/归档字节差异；固定Git归档输入用于云端和本地重现，不修改旧冻结代码。两次首次云端运行指标一致、参数有约2e-16的机器精度差，已明确12位小数数值合同后重新验证。

Actions、提交及产物回读以本次最终交付审计为准；本文件不单独宣称部署完成。
