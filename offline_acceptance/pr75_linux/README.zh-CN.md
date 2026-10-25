# PR #75：5 项 Linux 待验离线验收包

**准备状态：NOT_RUN。此次只准备和静态检查文件，没有运行此包、测试或安装环境。Linux 仍待验。**

本包基于 PR #75 的 `44c278fd4942f431a0f58c63ceedab0afe5078be` 准备。未来使用包含本包的 PR 检出；运行器记录实际 HEAD，并核对清单所固定的源码、配置及冻结文件。不要以这个准备包代替实际 Linux 结果。

## 目录和边界

- `run.py`：唯一验收入口，执行下述两个阶段；不安装依赖，不访问生产服务。
- `manifest.json`：固定原测试 ID、原严格工具以及仓库根目录 Python 源码、配置、60 个冻结文件的哈希。
- `requirements.txt`：预置依赖说明，当前不执行安装。
- `PREPARED.json`：仅记录准备和静态检查状态，不包含验收成功声明。
- `SHA256SUMS`：包内文件校验；不会包含原件、模型内容或历史数据。

这是一个与匹配仓库检出配合使用的覆盖包，不是完整仓库或环境镜像。ZIP 不含模型、历史数据、CSV 原件、SDK、wheel 或解释器。匹配的完整源码/冻结资产和 Linux 依赖须提前离线备齐；缺文件或哈希不符时退出非零，不能跳过。

只运行原有测试和未修改的严格工具；不调用发行、采集、评分、GitHub Run now、云端自动化或触发器。测试的合成文件放入新输出目录下的临时区，不向仓库写入；不修改冻结模型、协议或原始历史。

## 预置环境

|项目|要求|
|---|---|
|系统|真实 Linux；`sys.platform`、内核系统名均须为 Linux；Windows 模拟不接受|
|Python|CPython **3.12.14**，用 `-I -B -X utf8`，不能使用 `-O`|
|第三方依赖|Linux 原生 **NumPy 2.3.5**，已安装在所选解释器/venv 的 site-packages|
|Git|预先可用的 Git，本地检出有可读 HEAD；运行时不 fetch|
|文件系统|允许创建真实文件和目录符号链接；证据输出支持同目录硬链接、写入与 fsync|
|网络|预先离线/隔离的运行环境；不需要任何云凭据。运行器另阻断测试进程的连接、DNS 和 datagram 发送|
|空间|原源码/冻结资产之外，建议至少 1 GiB 可写余量；本包不执行 16/41 GiB 容量压力测试|

这 5 项不需要 sklearn、botocore、OSS SDK 或两份已取回的历史原件。不允许为通过而放宽 NumPy/源码哈希要求、替换 symlink 为 junction/hardlink，或将失败改为跳过。`-I` 会忽略 `PYTHONPATH`；依赖须由指定解释器本身提供。

本次不提供或执行安装命令。Windows 缓存中的 `.pyd`/Windows wheel 不能当作 Linux 依赖；以后若需准备 wheelhouse，应由环境负责人离线提供适用于 Linux CPython 3.12 的完整、核验过的依赖集合。

## 待验 5 项

1. `test_phase2_research.Phase2ResearchTests.test_output_symlink_rejected`
2. `test_phase2_research.Phase2ResearchTests.test_symlink_model_cannot_pass_manifest_verification`
3. `test_pit_retention_preparation.PreparationTests.test_latest_symlink_cannot_overwrite_outside_file`
4. `test_pit_retention_preparation.PreparationTests.test_output_directory_symlink_cannot_redirect_package_writes`
5. `test_prediction_release_control.Release.test_release_namespace_parent_symlink_rejected`

阶段一按上述顺序执行恰好 5 项，并核对实际 ID 与通过 ID，不接受替换或重复充数。

阶段二在同一进程中调用**未修改**的 `offline_linux_inventory_checks.main()`，其 4 项为上面的第 3、4 项，以及：

- `test_inventory_capacity.CapacityTests.test_destination_ancestor_link_cannot_write_outside`
- `test_inventory_capacity.CapacityTests.test_stage_root_link_is_rejected_before_download`

因此预期 **5 + 4 = 9 次执行，7 个唯一测试**；两项重叠是保留既有严格工具的验收边界，不把它们报成新增测试。运行器只在 unittest 的开始回调处观察并转发，记录严格阶段实际执行 ID，既不改测试内容，也不改变原工具的通过/失败判定；最终核对实际总数和唯一 ID 数。

## 将来执行命令（此次不要执行）

将包解压/保存到匹配检出的 `offline_acceptance/pr75_linux/`。以下路径都是占位示例，使用已经存在的离线环境；不要在当前 Windows 会话照抄执行。

```sh
set -eu
cd /absolute/path/to/doge-data-collector
sha256sum -c offline_acceptance/pr75_linux/SHA256SUMS

/absolute/path/to/prepared-venv/bin/python -I -B -X utf8 \
  offline_acceptance/pr75_linux/run.py \
  --repo /absolute/path/to/doge-data-collector \
  --output-dir /absolute/path/to/existing-evidence-parent/pr75-linux-run-001
```

输出的父目录必须已存在；`pr75-linux-run-001` 必须不存在，且位于仓库之外。再次使用同一输出目录会拒绝执行，绝不覆盖旧证据。失败输出保留供检查；不要通过删除旧证据或自动重跑来获得 PASS。

运行器对源码清单和包自身做前后核验，发生异常时仍尝试后核验并记录异常；禁止 Python bytecode 写入，记录实际 Git HEAD、解释器、NumPy、系统和内核。测试进程的网络阻断不等于已经验证内核网络 namespace；`kernel_network_isolation_verified` 明确为 false，系统离线条件应由运行环境本身提供。

## 证据输出

|文件|用途|
|---|---|
|`environment.json`|完成环境核对后的解释器、NumPy、Git HEAD、系统和内核信息|
|`five-tests.log` / `five-tests.json`|原 5 项的日志、实际执行/通过 ID、失败、错误与跳过详情|
|`strict-inventory.log` / `strict-inventory.json`|原严格 Linux 工具的日志与原生报告|
|`summary.json`|本包最终结论、时间、前后输入哈希、两阶段状态及异常详情|
|`evidence-sha256.json`|实际生成的上述日志/JSON 的 SHA256，包含 summary 本身|
|`temporary/`|私有合成测试临时区；通常测试结束后为空，保留外层目录|

JSON 先在同目录完整写入、flush/fsync，再用 `os.link` 独占发布，拒绝替换已有证据。若删除私有临时文件被系统拒绝，会返回失败并保留异常；已完整发布的 JSON 可能仍在，但不能据此认定整个验收成功。这里不声明目录崩溃持久性、任意恶意并发或硬杀后的事务回滚保证。

预检失败时，尚未执行的阶段文件可能不存在；`summary.json` 应为 `NOT_VERIFIED` 并记录原因。若连输出目录或证据写入都失败，以非零进程退出和 stderr 为准，不能把缺少报告当成成功。

## 零跳过验收标准

必须**同时**满足：

1. 原包 SHA256SUMS 及仓库输入清单核验通过，真实 Linux 和真实文件/目录 symlink 探针通过。
2. 原 5 项实际 ID、通过 ID均与上表完全一致，恰好 5 项；失败、错误、跳过均为 0。
3. 原严格工具报告 `tests_run=4`、`linux_verified=true`；失败、错误、跳过均为 0，工具无异常退出。
4. 运行前后源码、配置、包自身和 60 个冻结文件核验一致；实际执行总数为 9、唯一 ID 数为 7。
5. `summary.json` 为 `status=PASS` 且 `linux_verified=true`，证据文件完整并与哈希表一致，整个运行器退出码为 **0**。

只看 PASS 字段、退出码、部分测试日志或 Windows mock 中任意一项都不够。任何 skip、缺件、依赖不符、改动输入、取消、异常或输出失败均不能关闭 Linux 待验。

本包准备完成不改变 PR 草稿状态，也不授权合并、部署、生产补跑、新增触发器或云端操作。此前离线汇总的 1,314 项通过与这 5 项待验保持原口径，直至将来真实执行并生成有效证据。
