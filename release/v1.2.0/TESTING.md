# v1.2.0 Local Desktop MVP 验收

## 用户验收

在 macOS Apple Silicon 上执行：

1. 双击 `Sage.app`，等待主界面出现；
2. 选择 `Local`；
3. 点击“选择文件夹”，在 macOS 原生目录选择器中选择一个空目录或已有 Markdown 学习目录；也可以直接粘贴绝对路径；
4. 填写 Provider 名称、OpenAI-compatible Base URL、API Key 和默认模型；
5. 点击探测，确认 Provider 为 connected；
6. 在 Assistant 发送一条普通问题；
7. 创建一个学习任务，例如“学习 RAG 的 RRF 融合”；
8. 确认首轮进入 Knowledge-first 流程，知识不足时按策略进入只读 Research；
9. 打开生成的学习材料，确认有来源标题、URL 和 citation；
10. 刷新页面，确认任务仍显示正确的 stage 和 next action；
11. 退出 App，确认 Sage sidecar 进程已结束；
12. 再次启动并打开原任务，确认可以从 checkpoint 继续。

## 自动化门禁

```bash
# Python desktop contracts
PYTHONPATH=packages/sage_harness:. .venv/bin/python -m pytest tests/desktop -q --tb=short

# Frontend unit tests
npm --prefix frontend run test -- --run

# Frontend build
npm --prefix frontend run build

# Rust host contracts
cargo test --manifest-path frontend/src-tauri/Cargo.toml
cargo fmt --manifest-path frontend/src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path frontend/src-tauri/Cargo.toml --all-targets -- -D warnings

# Whitespace
git diff --check
```

## 通过标准

- `.app` 能启动，sidecar handshake 和 health 通过；
- 首次设置点击“选择文件夹”能打开原生 picker，选择后的绝对路径能回填并通过 onboarding 校验；取消 picker 不会误提交；
- Provider Key 不出现在前端、URL、日志、SQLite 正文和诊断包；
- 普通学习请求没有写入、Patch、Shell、MCP 或子 Agent 副作用工具；
- 学习材料能读取 citation，Research 失败时返回明确的可恢复状态；
- 冻结 sidecar 的学习 smoke 能完成 `knowledge_pending → knowledge_ready → synthesize_pending → artifact_ready`，并在重启后恢复到 `artifact_ready`；
- 刷新或进程重启后任务不丢失、不重复执行、不扩大权限；
- 退出后 `sage-desktop`、launcher 和 sidecar 进程数量为零。

前端回归还覆盖 picker 调用参数、路径回填、取消、原生异常转中文提示，以及错误码不出现在首次设置界面。

桌面默认关闭 Web Search/Web Fetch Provider；知识不足时的 `source_gap` 是预期的可恢复状态。自动化通过只证明协议、持久化和恢复边界，不证明线上模型质量、Web 新鲜度、生产准确率或 SLA。
