# V7 Beta Testing

> Last verified against: `dev/sage-v7@7a26197` (2026-07-24)

本页提供当前可执行的验证入口。任何发布结论都应记录 source ref、命令、退出码和失败项；
不要只复制一个会快速失真的测试数量。

## 1. 环境预检

```bash
python --version          # 3.12+
node --version            # 24，与 CI 一致
docker compose config -q
git status --short
```

首次安装：

```bash
bash scripts/bootstrap-dev-env.sh
cd frontend && npm ci && cd ..
cp .env.example .env
```

在 `.env` 配置至少一个模型 Provider 后运行：

```bash
SAGE_DEV_CHECK_ONLY=1 bash scripts/dev.sh
bash scripts/dev.sh
```

确认 Web、API 和 health 分别可访问：

- `http://127.0.0.1:5173`
- `http://127.0.0.1:8000`
- `http://127.0.0.1:8000/health`

## 2. 自动化门禁

### 完整质量检查

```bash
bash scripts/check.sh
```

### Harness 与 Practice 定向回归

```bash
pytest \
  tests/core/coding \
  tests/core/harness \
  tests/harness \
  tests/api/test_coding_routes.py \
  tests/api/test_coding_timeline_routes.py \
  tests/api/test_harness_capabilities.py \
  -q
```

### Knowledge 与 Cloud 边界

```bash
pytest \
  tests/core/knowledge \
  tests/core/knowledge/source_proposals \
  tests/api/test_knowledge_routes.py \
  tests/api/test_knowledge_job_routes.py \
  tests/api/test_knowledge_source_proposal_routes.py \
  tests/api/test_cloud_auth_routes.py \
  tests/api/test_cloud_workspace_routes.py \
  tests/api/test_cloud_model_provider_routes.py \
  -q
```

### 前端与生产构建

```bash
cd frontend
npm run test -- --run
npm run build
cd ..
git diff --check
```

## 3. 八个必跑场景

| # | 场景 | 操作 | 通过标准 |
| --- | --- | --- | --- |
| 1 | 首次启动 | 干净环境安装、迁移并打开 Assistant | 无空白页；health 正常；未配置模型时给出明确提示 |
| 2 | Assistant 主路径 | 新建任务并完成一轮回复 | 事件顺序正确；刷新后会话与终态一致 |
| 3 | Practice 证据链 | 读取文件、搜索、执行测试并查看 run trace | 工具结果、artifact、citation/diff 按类型展示，不伪造成回复 |
| 4 | 审批与停止 | 触发需审批操作，再分别拒绝和停止 | UI 与后端终态一致；刷新后不会继续执行或重复提交 |
| 5 | Knowledge 来源 | 摄取一个授权本地 Markdown 来源并提问 | 快照可追溯；回答包含可定位 citation；失败可重试 |
| 6 | Wiki 提案 | 创建、批准、拒绝并回滚一条提案 | 提案与已批准知识分层；历史版本和操作者证据可查 |
| 7 | 公开主页 | 在 `1440x900` 与 `390x844` 打开 `/#/public`，切换产品画廊并用回车提问 | 无横向溢出；真实 SSE 阶段、回答、citation 和资料包回执可见；私人 API 公网返回 404 |
| 8 | Sandbox 边界 | 在候选生产配置启动 Container Sandbox | rootfs、网络、资源、mount 与退出清理符合策略；不能回退宿主机 |

场景 8 是**公网开放私人 Harness**的硬门禁，不阻止不带工具权限的独立 Public Agent。
如果当前环境没有生产容器配置，应记录为 **未执行/阻断**，不能用本地
`local_workspace` 的成功结果代替。

## 4. 浏览器回归矩阵

- Desktop：Chrome/Chromium `1440x900`
- Wide desktop：`1728x1117`
- Mobile：`390x844`
- Theme：light 与 dark
- Interaction：hover、selection、blank reset、drawer、approval、reconnect

重点检查文本不遮挡、图谱标签与边可辨认、动态内容不引起布局跳动、控制台无新增错误。

## 5. 发布记录模板

```markdown
- Source ref:
- Environment:
- Backend quality:
- Frontend tests:
- Production build:
- Manual scenarios:
- Migration result:
- Open blockers:
- Decision: 可发布 / 继续开发 / 需修复
```

不要将 `.env`、Provider key、OAuth secret、邀请 token、用户文件内容或私有 timeline 附到
公开 issue、PR 或发布记录中。
