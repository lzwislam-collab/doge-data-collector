# DOGE PIT 与独立存证接入准备

本次交付独立准备工具，不改变已冻结模型、采集器、证据版本、历史记录或定时任务。
用户确认尚无外部存储，因此云端保留锁、独立存证与完整 PIT 验收仍未通过。

## 已实现的边界

`select_asof` 核验整个增强源本地账本的记录哈希、追加链、修订链接、原包字节与路径、有限数值、请求≤首次接收≤可用以及供应商发布时间≤接收时间。
同一观察的修订接收时间必须严格前进；按照决策截止时间选择当时可用的版本，后下载的历史值和后修订值不能进入早期决策。
陈旧最新修订不回退旧版本、不补零；元数据或原包异常直接拒绝。

接收陈旧度不是观察陈旧度。48小时只用于本次库存诊断，不是新注册模型的源时限。
调用方仍须使用实际供应商解析器核验值，分别制定并冻结各源的观察年龄/发布滞后限制。
这个工具不会授权增强源进入正式模型，且不能证明供应商首次公开时间或管理员从未修改本地历史。

准备器复用现有归档清单，纳入完整祖先清单与原包、预测/评分/绑定/执行字节，并补充增强源原始响应、账本、行情输入、候选上下文、版本注册和对应代码。
每个文件先按哈希验证，再放入一个包含逐文件清单的 ZIP；实际打包时间不会倒签成历史可用时间。
固定根路径、拒绝路径逃逸及符号链接，单包未压缩内容上限256MiB。不包括凭据目录或环境变量。

## 本地准备

先指定现有的清单文件，执行以下命令；需要在仓库根目录运行。

```text
python pit_retention_preparation.py prepare --manifest data/plus_evidence_v1_0/sources/manifests/<实际清单>.json
```

输出位于 `data/pit_retention_preparation_v1/`。无外部配置时状态必须是 `NOT_CONFIGURED`，`upload_performed=false`，`independent_retention_verified=false`。
准备命令不联网、不创建存储桶、不上传、不改保留期、不删除证据，不创建触发器。
准备失败时命令退出失败；旧输出不能当作本次成功，须核对退出状态与本次实际时间。

## 未来存储接入

支持的首个回读适配器是 AWS S3 Object Lock。未来由用户提供实际服务、桶、地区、独立所有者账户和保留期限，现阶段不创建收费资源或选择保留年限。
示例配置为 `config/pit_retention_receipt.example.json`，其中空字段必须由真实值替换；禁止把密钥放入配置或聊天。依赖已有SDK凭据链或未来配置的短期身份认证。

单个 ZIP 保存所有依赖原包；远端对象用内容哈希命名。未来上传请求应包含：

- `Bucket`、`Key=doge-evidence/<bundle_sha256>.zip`、`Body` 为真实ZIP字节；
- `ExpectedBucketOwner` 为实际存储所有者账户；
- `IfNoneMatch=*`，拒绝覆盖当前已存在的同名对象；
- `ChecksumSHA256` 为ZIP SHA256的Base64编码；
- `ObjectLockMode=COMPLIANCE`，`ObjectLockRetainUntilDate` 为用户确认日期。

本工具只准备包和执行只读回读，不包含上传执行器。上传前须确认真实桶已启用版本控制与保留锁，且权限和最短保留期匹配。
保存上传响应中的非空 `VersionId`，再读取这个固定版本；不得仅按最新同名Key验收。
保留期验证按“核验时剩余天数”执行。上传日期需留出核验耗时缓冲，不能因为延迟数秒而低于用户规定的最短剩余天数。

```text
python pit_retention_preparation.py verify --config /path/to/actual-receipt.json --expected-sha256 <ZIP哈希> --target-at <真实预测目标UTC>
```

只读核验条件：真实STS采集账户匹配配置、存储所有者与采集账户不同、ExpectedBucketOwner检查通过、版本控制与Object Lock启用、COMPLIANCE模式、最短剩余保留期、固定VersionId回读哈希与服务器LastModified。
缺SDK返回UNAVAILABLE，缺对象/版本/权限/期限等返回NOT_VERIFIED；错误信息不输出密钥或完整供应商异常。
`pre_target_package_existence_verified`只检验包的LastModified早于目标。
`pre_target_retention_verified`还要求本次实际核验完成在目标之前，证明核验当时锁已经有效。
目标过后才核验的对象即使早已存在，也不能证明保留锁当时已启用，因为锁可以后来添加；历史锁生效时间保持未验证。
云端包的LastModified不能证明包内数据更早的首次公开时间。
不同AWS账户不能证明不同的人类管理员；账户关闭等外部威胁仍是明确限制。

## 必须纠正的存证解释

S3 Object Lock保护具体对象版本。它仍允许同名新版本和删除标记；COMPLIANCE限制的是被锁定版本的修改、永久删除和缩短保留期。
因此“普通删除Key得到成功”不代表锁失效，验收必须绑定VersionId。
本工具不执行删除或覆盖探测；真实独立存证报告也不能声称做过这些测试。
若以后需要拒绝探测，应使用独立测试对象，取得明确授权，不能对正式证据试删。

依据：[AWS Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)、[AWS 条件写入](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)。

## 验收范围

本次可以验收准备工具和模拟云端失败边界。实际S3、独立首次公开时间、完整PIT、生产模型接入与收益均未验收。
未来要把新门槛接入模型，必须使用新的未来版本注册，不修改已有冻结代码或补写历史样本。

```text
PYTHONUTF8=1 python -m unittest -q test_pit_retention_preparation
PYTHONUTF8=1 python -m unittest discover -q
```

测试内成功云端回执均为模拟夹具，不是用户实际存储通过。
