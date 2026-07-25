# Context Governance v2.1 消融评测

## 1. 评测问题

旧实现的问题不是单纯“摘要不够好”：硬窗口阈值在 run budget 内不可达，同一摘要存在普通
消息与 durable channel 双重投影，且工具结果只要落盘就可能被过早清理。v2.1 将上下文改为：

1. Artifact offload + 有消费凭据的 recoverable pruning；
2. 单一 `summary_text` 的语义工作集压缩；
3. 六压力状态的硬窗口与运行安全层。

## 2. 固定协议

- 数据集：`evals/context_budget_v2_cases.json`；
- Case：13 条确定性任务，包含长工具链、Unicode、Artifact 中部探针和不可 offload 检索链；
- 实现：生产 cutoff、工具调用对保护、Artifact Store、分页读取与三层状态更新；
- 摘要：marker-preserving 确定性替身，仅验证机制和关键不变量；
- 指标：峰值/累计输入估算、checkpoint 内容、pruning / compaction 次数、摘要成本、最新用户、
  工具调用对、决策标记和 Artifact 探针；
- 报告：`evals/reports/context_budget_v2_1_2026-07-25.json`。

```bash
PYTHONPATH="$PWD:$PWD/packages/sage_harness" \
  python -m evals.context_budget
```

## 3. 分层消融结果

| Variant | 累计输入 | 相对 A0 | Peak | Checkpoint | Prune | Compact | Artifact 恢复 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A0 previous offload | 2,779,847 | 0 | 50,612 | 1,488,206 B | 0 | 0 | 0% |
| A1 offload 4k | 2,779,847 | 0 | 50,612 | 1,488,206 B | 0 | 0 | 0% |
| A2 recoverable prune | 2,244,450 | -19.26% | 49,830 | 871,342 B | 24 | 0 | 0% |
| A3 semantic compact | 2,083,494 | -25.05% | 31,016 | 756,331 B | 24 | 2 | 0% |
| A4 full | 2,083,494 | -25.05% | 31,016 | 756,331 B | 24 | 2 | 100% |

A4 相对 A0 的累计输入估算下降 `25.05%`，checkpoint 内容下降 `49.18%`，峰值从
`50,612` 降至 `31,016`。2 次确定性摘要估算消耗 `29,511` token，占 A0 累计输入
`1.06%`；扣除后净 token 下降 `23.99%`。13 条 case 的最新用户、存活工具调用对与决策
标记保留率均为 `100%`，Artifact 探针恢复率为 `100%`。

分层结论：

- A1 与 A0 完全相同，否决把生产 offload 阈值从 16 KiB 降到 4 KiB；
- A2 单独贡献 19.26% 累计输入下降，证明大部分长工具链可先用无模型成本的恢复引用治理；
- A3 再贡献 5.79 个百分点，并覆盖无法 offload 的长检索链；
- A4 不改变常驻输入，只补齐完整证据回载，因此恢复性与节省没有混为一个指标。

## 4. 阈值扫描

| Working set / keep | Prune | Compact | 累计输入变化 | Checkpoint 变化 | 扣除摘要成本后的净变化 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 32k / 12k | 24 | 2 | -25.05% | -49.18% | -23.99% |
| 48k / 18k | 5 | 1 | -7.27% | 以报告为准 | -6.36% |
| 64k / 24k | 1 | 0 | -1.27% | 以报告为准 | -1.27% |
| 80k / 30k | 0 | 0 | 0 | 0 | 0 |

四项安全门禁均通过，`32k/12k` 的预声明 selection score 最高，继续作为 Coding 默认值；
retrieval-only Surface 使用 `16k/8k`。六档 hard-window 状态没有被当成六次裁剪参与扫描。

## 5. 一次失败消融带来的修正

第一版 recoverable prune 只检查 `artifact_ref`，决策标记保留率曾下降。原因是“可从磁盘
恢复”不代表“模型知道需要恢复”。最终增加资格条件：工具结果必须已经被后续非空 AI 文本
承接，未消费的最新结果保留原预览。修正后再跑消融，决策标记恢复到 100%。

这条失败记录比最终数字更重要：Hermes / Claude Code 的 old-tool-output pruning 是有价值的
模式，但不能脱离 Sage 的 Artifact、Timeline 和模型消费状态直接照搬。

## 6. 证据边界

本报告只证明确定性机制成本和结构不变量。token 为 provider-neutral 估算，不是账单 usage；
耗时不含真实摘要模型网络调用；marker 保留不等于自然语言摘要正确。下一步真实模型评测必须
冻结 provider、model、temperature 和任务集，逐条记录 required facts、引用正确性、任务成功率、
P50/P95 延迟与实际 usage。
