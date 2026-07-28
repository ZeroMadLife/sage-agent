# Eval 报告保留策略

该目录保存与 source ref、Corpus revision 和 Eval revision 绑定的机器可读证据。v1.1.0 为了
复核 PostgreSQL、Embedding、recovery、消融和规模门禁，保留了完整 JSON；它们不是运行时
依赖，也不应被前端或 API 直接加载。

后续新增报告遵守以下规则：

1. PR 中提交可审阅的汇总、配置身份、数据 Hash、关键指标和失败分类。
2. 单个完整报告明显增大仓库时，优先存为 CI artifact，并在汇总中记录 SHA-256 与保留期。
3. 已进入 release 的报告不原地改写；修正时新增 revision，并说明替代关系。
4. 不提交请求正文、原始向量、Provider 凭据、用户内容或私有运行轨迹。

v1.1.0 之前已经提交的完整报告继续保留，以避免重写已评审历史；从下一次 Eval revision 起
执行上述紧凑化策略。
