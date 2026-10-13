# DOGE PIT 与存证准备部署验收

结论：准备工具已合并并接入现有自动流程，实际证据包已保存并下载核验；独立存储仍未配置，完整模型 PIT 尚未通过。

固定审计截止：2026-10-13 日本时间14:40:28（05:40:28 UTC）。

## 已完成

- [PR 17](https://github.com/lzwislam-collab/doge-data-collector/pull/17)：首次接收、修订与原包检查，独立上传包及S3固定版本回读适配器。
- [PR 18](https://github.com/lzwislam-collab/doge-data-collector/pull/18)：接到既有工作流末尾，每UTC日/代码版本准备一次，不增加cron。上传成功后才记录完成标记，失败可重试；超时写本次失败回执。
- [PR 19](https://github.com/lzwislam-collab/doge-data-collector/pull/19)：修复同小时零新增特征时的发布回读误报。冻结bind真实返回0时只跳过新发布检查；新增样本仍执行原严格核验，历史缺失分母不变。

[最终Linux CI](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38351494657) 600项测试通过，符号链接测试实际执行，冻结V2完整性通过。

[首次部署执行](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38350841245)成功，生成940对象、19,013,043字节的ZIP。服务器外层产物SHA256、内层包SHA256及每个对象哈希下载回读全部一致。实际打包时间日本14:21:01。

[定时重复执行](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38350879929)的准备步骤正确跳过重复打包；该次整体失败来自旧特征回读误报，失败记录保留。修复后的[整体执行](https://github.com/lzwislam-collab/doge-data-collector/actions/runs/38351563068)通过，同小时0新增不冒充新样本，历史审计继续执行，准备包没有重复上传。

原始供应商响应已重新解析，9,688条增强记录的来源、数值和记录字段匹配；该快照一致性不是供应商首次公开时间证明。

后修复的整体定时运行尚未在本固定截止时点完成验收；不能把push执行当作新的schedule执行。

## 尚未完成

独立存储账户、桶及用户确认的保留期仍不存在，因此S3上传/保留锁/固定版本实际回读均未验收，独立存证保持NOT_CONFIGURED。

日常ZIP和GitHub上传标记不是WORM；管理员可删除产物。每日库存只覆盖打包时已有证据，不证明当天后续每条预测已受保护。

首次接收元数据、原包哈希及追加链不能证明供应商更早首次公开时间。源观察陈旧度、供应商解析器语义核对及未来模型预注册接入仍需分别验收；准备工具没有授权增强源进入正式模型。

原模型、冻结特征实现、历史预测评分、cron和真实交易授权均未改动。此次工程部署不代表P0完整窗口、预测优势或盈利通过。

完整机器证据：[部署记录](../audit/PIT_RETENTION_DEPLOYMENT_20261013.json)。
