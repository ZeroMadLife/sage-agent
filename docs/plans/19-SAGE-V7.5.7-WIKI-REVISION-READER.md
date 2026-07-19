# Sage V7.5.7 Wiki Revision Reader 实施计划

## 目标

让用户从知识目录或 Wiki 列表点击页面后，直接在详情 Dock 阅读当前 canonical Wiki revision，而不是只能跳回图谱查看关系。

## 实施范围

- 后端增加当前 Wiki revision 的只读详情接口，返回页面元数据、revision 元数据和受限正文。
- 正文来自 SQLite canonical `knowledge_page_revisions.content`，不根据浏览器路径读取本地文件。
- Inspector 在页面节点上增加“正文”页签，按需加载并使用现有安全 Markdown renderer 展示。
- 从知识目录或 Wiki 列表打开页面时，自动打开详情 Dock；图谱节点点击在桌面端仍保留对话页签，手机端仍自动进入详情。

## 安全边界

- `page_id` 必须通过长度和字符集校验。
- 只返回页面当前 revision；未知页面返回 404。
- 响应使用 `Cache-Control: no-store`，正文最多 200000 字符并显式返回 `truncated`。
- 不返回知识根绝对路径、原始文件路径、密钥或 Provider 配置。
- V7.5.7 不开放任意历史 revision 读取，也不实现浏览器本地文件访问。

## 验收

- API 测试覆盖当前 revision、正文、no-store、未知及非法 page id。
- 组件测试覆盖正文加载、Markdown 安全渲染和加载失败状态。
- 页面测试覆盖 Wiki 点击后打开详情 Dock；图谱桌面选择仍保留对话。
- 前后端全量、生产构建、ruff、mypy、`git diff --check` 与真实浏览器联调通过。

## 后续边界

- 历史 revision diff、原始解析块查看和来源文件预览后续独立实现。
- Knowledge Chat 继续复用共享 Chat Harness，不在本阶段创建第二套对话运行时。
