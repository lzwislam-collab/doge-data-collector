# DOGE 第三层独立评审与纸面回滚

新增phase3_independent.py、phase3_runtime.py与phase3_lifecycle.py，只读R2真实前瞻账本。原18/14天/R2权重、协议、生效时间及触发器均不修改，无实盘订单。新统计方法与注册表由config/phase3_registration_v1.json锁定；必须实际main成功运行公开见证早于正式窗口和首个正式预测，才算有效预注册。

日常工作流接收现有R2发行完成回调，mode=routine仅诊断。full才形成完整审计：20个以内现场逐事件回读；更大账本必须提供完整batch运行ID。每个batch最多20事件/80原始运行locator，最多160次原始元数据REST请求；54批覆盖1080正式事件（工程样本另占账本位置）。分批运行应提前分散到多个小时，最后已成熟批次在审阅前补齐。full重新下载每个实际成功main批次的产物，核对服务端归档digest、实际job时间、精确冻结代码及源Git账本，合并完整覆盖与原始字节重放。最多64批次，汇集约256次REST；不能用未经验证的本地缓存替代，也不宣称汇集当次重新请求了全部旧REST元数据。原R2冻结audit_all不修改、不用于无界批量路径。任何缺失/重复/旧版本/源字节变动都拒绝。GitHub的工作流token接口额度仍需给其他任务留余量；不重复新建cron。

固定审阅窗口内使用预定完整90天1080槽，漏发/漏评/缺公开见证保留并拒绝准入，不删除后重新算分。工程canary始终排除。独立注册API检索限定为生效前，避免最新日常运行挤掉早期注册locator；也支持显式locator实际回读。

统计方法为固定lambda条件Hoeffding鞅界：每次最多64个单侧命题、两次固定审阅、家族上限10000。使用发行时已知的预测/Brier范围和行情掩码，未知标签缺失时取发行时可预测范围的下端；未发行时取最坏宽范围。掩码/范围不依赖目标是否评分成功。范围已预注册，lambda不根据观察波动或事后行情数量选择；没有独立同分布假设。估计量是观察窗口平均条件期望，不表示今后永久保持70%。每三日块用于描述覆盖，不当独立样本。

准确率下界>=70%，同事件方向基准改善>=1个百分点、Brier改善>=.002，四行情覆盖各>=20且无>.01 Brier损害。成本保留未截断名义/压力原始收益，要求原始候选和成对收益均值正；每个基准至少20个发行时不同敞口决策，掩码在发行时固定、使用完整1080槽分母，获胜频率下界须>.5*掩码覆盖率。不能按目标收益选择样本；同敞口没有成本差异，掩码为0，不能算经济胜利。经济统计界只证明不同敞口条件下的获胜频率；未证明无界原始收益均值置信区间或未来盈利。

首次注册PR36要求对cash和hold的全样本严格获胜率同时超过.5，在长仓/现金策略下不可能；原positive fixture对同敞口赋予不同收益不现实。保留该历史及实际工程核验，不把它称为可用成本准入。正式样本0时另以DOGE_R2_INDEPENDENT_FIXED_LOOK_R2_20261014/schema V2重新注册上述条件检验，并使用真实敞口共享long return/cash0的RED→GREEN测试。旧注册与旧批次不得作为新检验的有效凭证；R2预测协议/权重不修改。

第二层发布布局实际为model_archive.zip+model_package.json+registry.json+result_summary.json+future_protocol_draft.json，逐一校验1056模型原始哈希、候选记录、校准前标签隔离；不假设未提交的research.json/manifest.json存在。历史研究成绩不能支持前瞻晋升。

生命周期为明确标注simulation_only/synthetic的纸面状态演练；验证REGISTERED→SHADOW→REVIEW_READY→PAPER_ACTIVE→ROLLED_BACK链与精确冻结回退包。摘要检测意外损坏，并非密码学授权：自行重算摘要可伪造纸面状态，故该模块绝不能作为真实生产批准接口。真实生产切换和真实人工批准入口未实现，不能用几个approved字段开启生产。演练中的存储/审查字段是fixture，不是实际外部存储或独立人工批准证明。

实际外部不可改写留存仍未配置；非价格PIT不足因素继续隔离。正式前瞻仍按日本2026-10-20 09:00生效，首固定审阅日本2027-01-18 09:25；第二次不延长样本窗口。不凭回测/工程演练晋升。

参考官方成熟方法：
- [FreqAI保留历史真实预测](https://www.freqtrade.io/en/stable/freqai-running/)。
- [Microsoft Qlib滚动任务与记录汇集](https://qlib.readthedocs.io/en/v0.9.6/advanced/task_management.html)。
- [条件有界差与Azuma–Hoeffding证明](https://www.stat.berkeley.edu/~mjwain/stat210b/Chap2_TailBounds_Jan22_2015.pdf)。

工程发布须以实际Actions成功和产物回读为准；本文不把尚未发生的正式预测或未来独立评审写成完成。
