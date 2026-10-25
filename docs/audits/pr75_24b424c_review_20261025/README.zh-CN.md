# 24b424c 独立复审凭据

Linux原5项关闭结论有效。本轮修复两处可核验性缺口，未修改生产代码。

- identity-summary.json / identity-matrix.json：1292 + 16 + 4 + 5 + 2 = 1319，唯一ID合并，非本轮全仓重跑。
- receipts/*.public.log：历史全仓与25项补验的隐私安全ID/状态投影，保留原状态和引用行号；主机路径、堆栈和无关诊断已移除，不称原始日志。原件在本机保留且未改。
- receipt-publication.json：原件与公开投影两种SHA256、投影规则和边界。matrix的sha256核对公开文件，original_sha256记录私有原件。
- receipts/recovered-log.public.json：仅保留原4项passing日志字段；embedded_field/embedded_line按该日志字段读取行号。
- lean-offline-recheck.log/json：本轮新12项离线-O复验，精确ID和输入前后哈希。旧优化日志未找到，不冒称找回。
- freeze-and-identities.json：五份注册60文件及AST1319身份核对。293是源码/配置数量，不代表全部历史资产。
- recheck_lean.py：本轮定向离线复验复现入口，保留优化模式、阻断网络和拒绝跳过，不修改受测代码。

原Linux7份核心证据继续引用 ../pr75_linux_runtime_20261025/，本轮未重跑Linux验收。另行命名的integration_oss_sdk不加入1319。

结论针对本PR离线修复及冻结边界：无剩余P0/P1/P2阻断，可合并；按用户要求保持草稿、不合并。生产与云端验收范围未扩大。原始失败日志外发被自动审批拒绝，以上隐私安全投影为替代方案，没有绕过原始日志外发限制。

## 定向复验入口（仅需复现时使用）

公开入口已参数化适配仓库位置；原执行脚本与既有收据不改写。输出父目录须已存在，输出目录须不存在且在源码之外。

```sh
python -I -B -X utf8 -O docs/audits/pr75_24b424c_review_20261025/recheck_lean.py --repo /absolute/source/checkout --output-dir /absolute/evidence-parent/new-lean-run
```

`--help`仅查看参数，不执行测试。本轮已验证公开入口帮助参数；没有为此重复运行12项。
