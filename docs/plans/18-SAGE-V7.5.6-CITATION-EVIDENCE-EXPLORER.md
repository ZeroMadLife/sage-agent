# Sage V7.5.6 Citation Evidence Explorer 开发计划

## 1. 问题

V7.5.5 已能从知识图谱选择节点并查看 citation、chunk、Wiki revision 和 Source revision，但用户仍只能看到标识符，无法判断证据片段具体说了什么。

直接把服务器文件路径或原始文件读取接口开放给浏览器会扩大路径穿越、隐私泄露和 stale revision 风险。因此 V7.5.6 先从已经批准并进入索引的 citation 建立浏览闭环，再独立设计受权 Raw Source 预览。

## 2. 本切片

```text
图谱节点
  -> 一跳边的 citation_id
  -> GET /api/v1/knowledge/citations/{citation_id}
  -> 校验 workspace + private visibility + active revision
  -> 返回限长索引证据片段与双 revision
  -> Inspector 原位展开
```

用户在“详情 -> 证据”点击“查看证据片段”后，可以看到标题层级、页码和限长正文。切换节点时，旧节点的加载状态、错误和片段必须全部清除。

## 3. 安全契约

- 浏览器只提交稳定 `citation_id`，不提交绝对路径、Source Root 或任意相对路径。
- citation 必须匹配当前 workspace 中 `active=1`、`visibility=private` 的索引 chunk。
- stale、未知或伪造 citation 返回 404；格式不合法返回 422。
- 响应不包含 `raw_path`、服务器绝对路径、Source Root 配置或 Provider 凭据。
- excerpt 最多 3200 字符，并通过 `truncated` 明示截断。
- 响应使用 `Cache-Control: no-store`。
- 该接口只读，不生成 Wiki revision，不修改 Git，不触发 Agent 或 Memory。

## 4. 非目标

- 不直接读取 Obsidian、GitHub 或飞书原始文件。
- 不开放 PDF/图片二进制下载或多模态预览。
- 不把索引片段伪称为原始文档全文。
- 不在 Knowledge 页面临时复制 Chat Composer；直接对话等待共享 Chat Harness 接线。
- 不在本阶段实现正文 citation token 到具体行号的深链接。

## 5. 验收

- API 能从真实检索结果的 `citation_id` 读取相同 chunk 与双 revision。
- 伪造 citation、错误格式和 stale revision 均 fail closed。
- 响应不出现来源绝对路径。
- Inspector 原位展开证据片段，并显示标题层级和截断状态。
- 手机端自动进入详情后，可继续切换“证据”并展开片段。
- 后端测试、前端测试、ruff、mypy、vue-tsc、生产构建和 `git diff --check` 通过。

## 6. 后续

1. V7.5.6B2：受权 Raw Source block 预览，绑定 source revision、parser block 和访问审计。
2. H2.1：共享 Chat Dock 接入 Knowledge Surface，冻结选中 citation 上下文。
3. H2.2：回答正文 citation 标记跳转 Inspector 对应证据，并提示 revision 已失效。
4. V7.6：用 Golden Queries 评估图谱遍历是否值得加入 Hybrid RAG 召回。
