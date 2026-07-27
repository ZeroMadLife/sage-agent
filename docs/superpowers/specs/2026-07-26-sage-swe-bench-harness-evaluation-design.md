# Sage Coding Harness 的 SWE-bench 评测设计

> 日期：2026-07-26
> 状态：设计完成，尚未运行 SWE-bench，不包含 resolved rate

## 1. 评测对象

SWE-bench 只评估 Sage Coding Harness 在真实仓库 issue 上完成定位、编辑、测试和提交 patch
的能力。它不评估 Knowledge RAG、Memory 事实准确率或公开 Ask Sage 的问答质量。

一次 sample 的输入和输出固定为：

```text
dataset revision + instance_id + issue + base commit
  -> fresh isolated container
  -> Sage Harness trajectory
  -> final patch + run evidence
  -> upstream SWE-bench grader
  -> resolved / unresolved / infrastructure_error
```

## 2. 为什么采用 Inspect + upstream grader

Inspect Evals 负责 Agent 执行层：逐 sample sandbox、模型与工具轨迹、消息/时间限制、日志查看
和基础设施错误分类。最终 patch 继续交给上游 `swebench.harness.grading.get_eval_report`，以
`FAIL_TO_PASS` 全部修复且 `PASS_TO_PASS` 不回归作为 resolved 判定。

这样可以同时保留 Sage Harness 的 Timeline、tool call、context pressure、artifact offload 和
恢复证据，又不自定义一个对自己更有利的评分器。需要发布或横向比较时，再用官方
`swebench.harness.run_evaluation` 对导出的 predictions 独立复评分。

## 3. 固定运行边界

- 首个 smoke 使用 `SWE-bench Verified Mini`，通过后再固定 50 条分层样本；不直接跑全量。
- dataset revision、base image digest、Sage commit、模型 id/revision、prompt revision 全部入报告。
- 每个 sample 使用独立容器，默认禁网，限制 CPU、内存、进程数、wall time 和 tool timeout。
- 测试命令由 task image 与官方 test spec 决定，Agent 不能改评分脚本。
- patch apply failure、测试失败、timeout/OOM、容器故障分开统计；infra error 不计为模型错误，
  也不能从分母中静默删除。
- 只把仓库和 issue 放入 sample；不挂载个人 Knowledge、Memory、凭据或宿主工作区。

## 4. Sage Adapter

Inspect solver 只适配 Sage 已有 Harness，不重写一套 agent loop：

1. 用 instance metadata 创建受限 coding session；
2. 将 issue 作为用户任务，工作区指向 sample sandbox；
3. 复用 Registry、Permission、Policy、ToolExecutor、ContextController 和 Timeline；
4. 等待 run terminal，导出 workspace diff；
5. 只允许符合 base commit 的 unified diff 进入 predictions；
6. 将 patch 交给 upstream grader，Sage 自身测试结果只作为过程证据。

## 5. 指标

官方结果：

- resolved rate；
- `FAIL_TO_PASS` success；
- `PASS_TO_PASS` success；
- patch apply rate；
- infrastructure error rate。

Harness 诊断：

- P50/P95 wall time、input/output tokens、model/tool calls；
- 首次正确文件定位率、首次测试前 tool calls；
- context pressure 分布、compaction 次数与净 token 节省；
- artifact offload/load 次数与恢复成功率；
- sandbox/policy/approval 阻断数；
- terminal 完整率、Timeline replay 一致率。

## 6. 消融

同一批固定 instance 至少比较：

| 变体 | Context 治理 | Artifact offload | Checkpoint/恢复 | 受控 Sandbox |
| --- | --- | --- | --- | --- |
| C0 | 基础截断 | 否 | 否 | 是 |
| C1 | 三层治理 | 是 | 否 | 是 |
| C2 | 三层治理 | 是 | 是 | 是 |

Sandbox 不做关闭消融，因为关闭隔离会改变风险边界，也无法作为可发布配置。模型、prompt、
数据集、镜像和预算必须保持一致；先比较 mechanism，再根据失败轨迹优化，不反复挑样本。

## 7. 分阶段门禁

1. `S0 Adapter`：2 个本地合成 sample，验证 patch、轨迹、timeout 和 infra error 契约。
2. `S1 Smoke`：Verified Mini，单并发验证镜像与官方复评分一致。
3. `S2 Fixed-50`：按仓库、语言、修复规模分层，形成第一次可比较基线。
4. `S3 Ablation`：固定样本和模型运行 C0-C2，分析长任务失败与治理收益。

当前只完成设计，尚未安装/运行 Inspect Evals，也没有 SWE-bench resolved rate；README 和简历
不得提前写结果。

## 8. 一手资料

- SWE-bench 官方仓库：<https://github.com/SWE-bench/SWE-bench>
- SWE-bench Harness API：<https://www.swebench.com/SWE-bench/api/harness/>
- Inspect Evals SWE-bench：<https://github.com/UKGovernmentBEIS/inspect_evals/blob/main/src/inspect_evals/swe_bench/README.md>
- Inspect Sandboxing：<https://inspect.aisi.org.uk/sandboxing.html>
