# Sage 主对话回答与前端投影 Contract Gap

> 日期：2026-07-22
>
> 提交对象：Harness 后端会话
>
> 边界：本文只描述契约缺口，前端线不修改 `core/**`、`api/**`、`packages/sage_harness/**`。

## 1. 问题

当前模型回答经常把“来自哪些内部来源、缺少哪些系统证据、使用了哪个运行结构”直接写进正文。对普通用户而言，这些内容抢占答案首屏，降低任务完成感；前端无法可靠地通过字符串裁剪区分必要解释和模型正文。

## 2. 期望契约

主回答建议拆成稳定的语义字段，而不是要求前端解析自然语言：

```text
answer
citations[]
limitations[]
next_actions[]
process_summary?   // 用户按需展开
```

- `answer`：直接回答用户问题，不默认重复内部检索过程；
- `citations`：只包含允许展示的来源标题、公开引用和 evidence ref；
- `limitations`：只有会实质改变结论时显示，使用用户语言；
- `next_actions`：最多 3 个与目标相关的后续动作；
- `process_summary`：工具、MCP、子代理、来源命中/缺失等运行事实，默认折叠到完整运行详情。

## 3. 展示原则

1. 回答首段先给结论或可执行结果，不以“根据当前上下文”“我无法看到界面”开场。
2. 不把 `run_id`、`revision`、`sequence`、tool name 写进普通正文。
3. 来源不足但仍可回答时，将限制收敛成一条短说明；无法安全回答时才阻断。
4. Citation 必须来自公开 timeline evidence 或后端冻结 receipt，前端不自行拼接可信来源。
5. Knowledge 节点提问在提交时绑定 frozen `surface_context`；正文可称“基于所选节点”，不回显内部引用值。

## 4. 前端降级

契约实现前，前端只做以下安全降级：

- 日常 UI 隐藏 Run/Graph 技术标识；
- 完整运行详情继续显示 timeline 事实；
- 不裁剪、不重写模型自然语言正文；
- 不把前端推断出的来源或缺口标记为后端事实。

## 5. 验收

- 普通问答首屏不出现内部 ID、tool name 或系统自述；
- 用户展开“完整过程”后仍能查看工具、引用、审批和恢复事实；
- 刷新或恢复后 `answer/citations/limitations/next_actions` 可由 timeline 重放；
- Knowledge frozen context 能绑定 thread/run/message、graph revision、selection ref 与 page/source revision；
- 旧客户端继续可消费纯文本 `answer`，新增字段保持向后兼容。
