# Sage 开发环境启动指南

本指南覆盖当前 Sage 本地开发链路：Python/FastAPI、Vue、PostgreSQL、Redis 与 SearXNG。
项目不再内置早期旅游原型的地图、天气或景点 MCP Server。

## 1. 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 24（与 CI 一致）
- Docker Engine 与 Docker Compose v2

```bash
python3 --version
uv --version
node --version
docker compose version
```

## 2. 安装依赖

在仓库根目录执行：

```bash
bash scripts/bootstrap-dev-env.sh
npm --prefix frontend ci
cp .env.example .env
```

PyCharm 或 VS Code 的 Python 解释器选择仓库内 `.venv/bin/python`。不要复用旧的 Python
3.11 环境；当前 Harness 依赖 Python 3.12 与 LangChain 1.x。

## 3. 配置模型与应用

本地 `.env` 至少配置一个可用的模型 Provider。以下示例使用 DeepSeek：

```bash
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek:deepseek-v4-flash
LLM_LIGHT_MODEL=deepseek:deepseek-v4-flash
```

数据库与 Redis 使用 Compose 默认值即可：

```bash
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=sage
POSTGRES_PASSWORD=sage_dev
POSTGRES_DB=sage
REDIS_HOST=localhost
REDIS_PORT=6379
```

Knowledge 检索投影默认使用 SQLite。完成 PostgreSQL schema migration 后，可以显式切换为
GIN + pgvector exact backend；canonical Wiki/proposal store 仍保留在 SQLite：

```bash
KNOWLEDGE_INDEX_BACKEND=postgres
KNOWLEDGE_WORKSPACE_ID=knowledge-local
# 默认复用上面的 POSTGRES_*；只有独立数据库时才设置此项。
KNOWLEDGE_POSTGRES_DSN=
```

切换前先运行 `python scripts/migrate_knowledge_index_postgres.py --force`。第一版不会创建
HNSW/IVFFlat；若 PostgreSQL 不可用，显式配置的 postgres backend 会启动失败，不静默回退 SQLite。

`.env` 已被 Git 忽略。不要提交 Provider key、OAuth secret、访问口令或用户数据。

## 4. 启动本地服务

最短路径：

```bash
bash scripts/dev.sh
```

该脚本会检查开发环境、启动 Compose 基础设施，再启动 FastAPI 与 Vue。只做预检可运行：

```bash
SAGE_DEV_CHECK_ONLY=1 bash scripts/dev.sh
```

默认入口：

- Web：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:8000`
- Health：`http://127.0.0.1:8000/health`
- Search：`http://127.0.0.1:8088`（仅本机）

也可以只启动基础设施：

```bash
docker compose up -d
docker compose ps
docker exec sage-postgres psql -U sage -d sage -c "SELECT version();"
docker exec sage-redis redis-cli ping
```

开发环境默认执行幂等 schema 迁移。需要单独执行时使用：

```bash
.venv/bin/python -m db.migrations
```

生产环境不在应用启动时自动迁移，发布流程必须显式执行迁移并保留回滚点。

## 5. 运行质量检查

后端完整门禁：

```bash
bash scripts/check.sh
```

前端测试与两套生产构建：

```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run build
npm --prefix frontend run build:public
```

提交前再检查空白错误：

```bash
git diff --check
```

## 6. MCP 与外部能力

Sage 保留通用 MCP catalog、连接池和 Harness adapter，但仓库不再硬编码特定旅游服务。
MCP Server 应通过运行配置注入，并在设置页或 `/api/v1/coding/mcp/servers` 检查脱敏后的发现
状态。真实外部服务需要独立凭据和契约测试，单元测试不依赖这些凭据。

## 7. 常见问题

### 端口被占用

```bash
lsof -i :5173
lsof -i :8000
lsof -i :5432
lsof -i :6379
lsof -i :8088
```

停止占用端口的进程，或在 `.env` / Compose 中调整对应端口。

### 数据库迁移失败

先检查 `docker compose ps` 与 `.env` 中的 PostgreSQL 配置，再单独运行迁移命令查看完整错误。
不要用删除 volume 代替迁移修复；`docker compose down -v` 会清空本地数据。

### 模型未配置

Health 仍可用，但 Assistant 无法完成真实模型调用。检查 Provider API key、base URL、模型名
以及设置页显示的 capability 状态。

### worktree 没有 `.venv` 或 `node_modules`

本项目的依赖目录不会随 Git worktree 自动复制。可在该 worktree 重新运行 bootstrap 与
`npm ci`，或在只读复用依赖的前提下显式指定解释器；不要把依赖目录提交到 Git。

## 8. 发布前补充验证

本地全绿不等于可以合入 `main`。候选版本还需要确认精确 source SHA、迁移结果、CI、前端
视口回归、Public/Private smoke、Canary 与回滚。完整清单见
[v1.0.0 验收文档](../release/v1.0.0/TESTING.md)。
