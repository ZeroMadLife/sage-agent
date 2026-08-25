# Sage 产品化基础 V1 交付计划

## 目标

交付一个可复用的本地一键启动入口，让新用户从 checkout 到 Assistant 页面只需要一个命令，并保留当前 `dev.sh`、Docker Compose 和产品运行时作为唯一事实来源。

## 切片

### Slice 1：Quickstart 预检与初始化

- 行为：创建安全 `.env`、复用 Python bootstrap、安装前端依赖、执行现有 dev 预检。
- 公共入口：`bash scripts/quickstart.sh --check`。
- 验收：首次运行、已有配置、缺少依赖、Provider secret 脱敏四类契约测试。
- 非目标：启动 API、修改数据库和运行真实模型调用。

### Slice 2：Quickstart 启动

- 行为：预检通过后调用现有 `scripts/dev.sh`，保持 API/Web/Compose 的端口和环境变量契约；产品入口设置 `SAGE_DEV_RELOAD=0`，避免 Uvicorn 监听虚拟环境依赖目录导致长任务期间反复重启，直接运行 `dev.sh` 仍默认保留热更新。
- 公共入口：`bash scripts/quickstart.sh --start` 或默认命令。
- 验收：静态脚本契约、启动委托路径和现有 Web build。
- 非目标：在 quickstart 中复制服务进程管理或实现第二套 Compose。

### Slice 3：文档入口

- 行为：README 和 Getting Started 把 Quickstart 作为最短路径，并标明 local/private-canary/public 三种部署边界。
- 验收：文档链接、命令和当前脚本名称一致。

### Slice 4：真实学习闭环验收

- 行为：通过 Assistant 首页创建学习会话，在同一会话中完成“起点诊断 -> 一周任务 -> 当日练习 -> 复述要求”的连续两轮交互。
- 验收：后端 `/health` 返回 `{"status":"ok"}`；首轮运行完成并写入 7 条待办；第二轮读取上一轮上下文，将 `AABAAA` 计算为 L1、更新 Day 1 待办；刷新会话 URL 后消息、运行摘要与继续入口仍存在。
- 非目标：本切片不自动创建长期 Thread Goal，不把一次会话的待办台账包装为跨用户长期记忆。

## 依赖与风险

- Python 环境依赖 `uv` 和 `.python-version`；脚本只负责检查和调用，不安装系统级 Python。
- Docker 仅由现有 `dev.sh` 负责启动；quickstart 不绕过 Compose 健康检查。
- 本机 `.env` 可能存在 SearXNG/PostgreSQL 漂移；这属于环境修复项，不由本切片静默改写。
- 模型可能在 `tool_search` 提升延迟工具前提前尝试调用；Harness 会记录失败、继续提升并重试，产品验收以最终工具结果和回答是否完成为准。
- 生产部署继续由 `deployctl.py` 管理，不接受 quickstart 读取生产密钥或远程执行。

## 完成门禁

```bash
bash -n scripts/quickstart.sh
PYTHONPATH=packages/sage_harness:. .venv/bin/python -m pytest -q tests/scripts/test_quickstart.py
npm --prefix frontend run build
git diff --check
```
