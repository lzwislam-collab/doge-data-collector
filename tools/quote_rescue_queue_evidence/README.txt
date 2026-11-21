独立补采：离线队列分段附录

范围
本目录只有一个离线诊断模块、它的测试和本说明。它读取现有单run验证链保存的
quote-score-fix-run<ID>-evidence.json及-assessment.json，另存队列附录。
不执行旧验证脚本，不重新评分，不改旧结论。旧脚本有写回assessment的副作用，
不能为了生成本附录而重新执行。
没有网络调用、collector模块导入、GAS变更、workflow变更、dispatch或恢复入口。

用法（cd tools/quote_rescue_queue_evidence；Python 3.11+，无第三方依赖）
python quote_rescue_queue_evidence.py --evidence EVIDENCE.json --assessment ASSESSMENT.json --output NEW_QUEUE_APPENDIX.json
将示例文件名替换为已有单run证据、已有核验结论和一个全新输出路径。
python -m unittest -v

输入
--evidence：已有单run证据，包含run、jobs、可选result。
--assessment：可选的既有独立核验结论；必须同run、同attempt及同publication/target。
  支持已有两种VERIFIED_NATURAL_WINDOW_RESCUE状态和两种旧行保留字段。
  旧结论没有attempt时只允许关联run_attempt=1，不能用于重复运行。
  关联成功收据还要求同attempt的rescue job为completed/success，关键关联时间均存在、
  为有效UTC且与任务执行区间一致；双方同时缺失时间不算一致。
  本模块只交叉关联旧收据，明确replayed=false，不把它变成新的原始报价/评分验收。
--jobs：仅当原证据缺完整jobs时，显式提供已经通过原单run GET保存的完整jobs响应。
  模块本身不会下载。要求同run及明确同attempt的唯一rescue job；verify job不参与。
  不完整分页、重复匹配job或错误身份拒绝处理，缺失attempt则保留未知job。
  显式jobs响应必须提供准确total_count；整个响应未保存时标JOBS_RESPONSE_UNAVAILABLE。
--caller-record：可选，必须含run_id、run_attempt、dispatch_requested_at_utc。
  没有实际保存dispatch时钟就不提供；不拿run_created或GAS handler开始时间代替。
--concurrency-snapshot：可选，必须含run_id、run_attempt、captured_at_utc、response。
  仅表示读取时的并发状态。即使当前为空，也不证明历史无锁。queue_cause始终UNKNOWN。
--output：只能写全新文件，目录须已存在。已存在输出、输入路径或符号链接均不覆盖。

输出及时间口径
记录dispatch→run创建、run创建→job创建、job创建→job开始、job开始→采样尝试，
以及逐目标真实observed→远端回读。不能以run_started_at替代job.started_at。
result中的capture.events提供采样尝试起点，quotes提供真实observed时钟；前者缺失时
保持null，不从交易所报价ts推测网络开始时间。重试事件须从attempt=1连续且时间有序。
时间必须显式UTC；缺失为null，非法/倒序产生issues，不取绝对值或夹成零。
skipped job不能产生runner执行时钟。失败/排队run也可生成诊断，但不提升为补采成功。
旧result格式没有独立artifact/attempt来源证明，因此attempt>1只记录明确匹配的job时钟，
不采用result中的采样/目标/发布时钟，并标RESULT_ATTEMPT_UNPROVEN。
按原target+20分钟采样、target+25分钟报价/回读边界列出时钟诊断标志；不重评冻结成绩。
所有读取文件附SHA256；不改输入。结果属于QUEUE_EVIDENCE_ONLY，
rescue_acceptance_evaluated=false、performance_acceptance_evaluated=false。

保持不变
没有修改GAS每轮GET、空闲2GET、共享锁、queue:max、SKIP_EXISTING_RUN、一次dispatch、
获锁后fresh main、20/25分钟规则、旧行不可变规则、5秒平台完整主均值、<=600秒间隔、
<=30分钟日完整预算。这里没有运行GAS性能验收；本地单元测试不能宣称5秒实测通过。
不把缺日志、慢轮、历史missing、20秒额外触发、停机证据删除或变成成功。

局限
本模块不能消除GitHub托管runner排队，也不能补回历史报价。没有当时的锁租约记录，
就不能把job等待归因为共享锁或某一项runner容量原因。原单run验收结果仍是独立来源，
本附录既不替代它，也不重新执行它。正常collector工作流输入会被拒绝。

完整原始测试、审阅和保护核对材料保存在本地verification/；PR仅包含工具、测试和本说明。
仓库测试内嵌已核验run39024893378的准确元数据投影，不依赖本地历史文件；没有新造报价或重跑。

本地核验与独立审阅
- python -m unittest -v：36项通过；包含身份/attempt、缺失与倒序时钟、失败/排队/skipped、拒绝覆盖及历史run关联。
- 独立审阅review_queue_evidence发现的5项校验问题已修正；复审36项通过，无遗留阻断项。
- 本轮仅为离线工具测试，未执行旧验证器、工作流、GAS或5秒平台性能复测。
- 原始36项测试日志SHA256：8e5bb306b243cf1c26fd26c5665369c99b9306b9cd895a6244d3430e997736eb。
- 内嵌样本原始evidence SHA256：c51f4bee7430c63463e16a218d9bc19b32d4f79bb1b9fc735f978c27297d39ca。
- 内嵌样本原始assessment SHA256：5020689ec66c9af0a828756699366e9b25927c0b2244e1872089cda8f84a6beb。
