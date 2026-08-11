# Sage 架构图资产

## 唯一总体架构图

- [中文矢量图](sage-harness-rag-integrated-v1-zh.svg)
- [中文 PNG](sage-harness-rag-integrated-v1-zh.png)
- [Graphviz 结构源](sage-harness-rag-integrated-v1-zh.dot)

总体图把 Harness、Agentic RAG、安全执行、恢复与分层 Eval 放在同一条端到端链路中。SVG 是中文可读的权威版本，PNG 用于 README 和简历预览，Graphviz 源用于结构校验与后续维护。

## 专题图边界

`task-dag/`、`book-rag-postgres/`、`context-assembly/` 等目录中的图只解释单个专题，不再作为总体架构图使用。`pic/` 下的生图结果或视觉草图仅作为设计参考，不承担组件事实和接口契约。
