# 三项剩余工作的可执行补齐

结论以实际CI、主分支运行和产物回读为准。取消的是必须第三方管理、必须不同AWS账户；不可改写原文、提前留证、缺失分母和统计门槛保留。原18候选/13冻结文件、生效时间2026-10-16 00:00 UTC均不改。

## 用户自主管理归档

新 `config/evidence_custody_v2.json`、`user_managed_retention_v2.py` 允许同账户，但永远不声称独立管理员验证。实际验收读取启用的Object Lock/Versioning、固定非null版本、COMPLIANCE模式、固定审核保护截止、实际身份和原文SHA256；上传之后再读取对象版本核验，不信任本地verified标记。提供源码/冻结模型/源CSV/最近72h真实预测及原始目标证据包。锁定时间逐记录对照预测目标或评分窗口；晚补锁不变成提前留证。

目前没有云账户和资源。默认不调用AWS、不上传、不创建资源。仅当用户确认实际资源与权限，并设置现有仓库变量 DOGE_WORM_UPLOAD_ENABLED=true、DOGE_WORM_BUCKET、DOGE_WORM_ACCOUNT、DOGE_WORM_ROLE_ARN 后，现有资源归档作业才运行。一次性账户、付款和权限操作仍由用户确认；不要发密钥到聊天。

可审阅 `infrastructure/user_managed_archive_v2.json` 为同账户私有S3、730天COMPLIANCE默认保留、范围仅doge/evidence/的角色，GitHub OIDC信任仅本仓库main。模板未部署、没有AWS部署验证，不把JSON检查称云资源验收。角色不能删除对象/缩短锁定，账户注销等服务边界仍存在。[AWS Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)，[GitHub AWS OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)，[CloudFormation OIDC](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-iam-oidcprovider.html)。旧双账户验证器保留历史含义，不静默修改旧协议。

## 非价格PIT

现有OI/Funding采集器原文与历史连续性重放保留。新增source-publication实际成功job的服务器完成时间绑定固定Git SHA、精确原始HTTP文件和账本行；可用时间取首次收到和公开完成的较晚者。源作业完成可以在同一workflow后续job独立API回读，不依赖整个workflow先结束，也不依赖调用者verified=True。

使用24个已结束小时的预约覆盖分母。最新尚未公开、陈旧、未收取、过期资金费率均隔离，不回退旧版本、不补零。数据门禁通过只是新研究准备条件；仍须新候选注册、同事件消融与真实前瞻成绩。不会把原文公开当作已经发行预测或交易所签名。

工作流仅复用已有采集/模型审计/候选发行完成事件，无新cron或Apps Script触发器。先前自动采集38484459919原始捕获成功但Git推送fetch-first拒绝，已加入3次有限无强推重试，冲突终止；always产物保存首次原文，即使发布失败也不丢证据。不重构未保存的历史失败样本。

## 未来闭环

已安装发行器继续等待10月16日日本09:00。新归档证据包含真实事件和目标，外部存储真实开通后才能验证目标前锁定。未到时间不造事件，缺云资源不宣称WORM。未来实测1H/24H、足够覆盖及跨行情样本仍是待验证条件，70%无保证。
