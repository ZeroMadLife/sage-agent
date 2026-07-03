# TourSwarm M4 Demo Script

## 0:00-0:45 架构总览

- MCP Server 提供天气、景点、路线工具。
- LangGraph Supervisor 编排 Info、Recommend、Planning、Budget Agent。
- Redis + Mem0/Qdrant 提供短期与长期记忆。
- Phase 4 新增 FastAPI/WebSocket、确定性验证器与 Vue3 工作台。

## 0:45-2:00 Web 全流程

输入：`周末去杭州2日游预算500元喜欢美食`

展示：
- 左侧 Agent 进度事件。
- 右侧结构化行程时间轴。
- 预算摘要是否超支。
- 验证器结果。

## 2:00-3:10 记忆影响

先运行记忆 demo 或使用已有记忆，展示“喜欢海鲜”如何进入 prompt context 并影响规划。

## 3:10-4:20 验证器与指标

展示 `evals/travel_cases.jsonl` 和 `evals/run_eval.py`，说明 schema 有效率、验证器通过率、P95 延迟。

## 4:20-5:00 工程质量

运行：

```bash
bash scripts/check.sh
cd frontend && npm run test -- --run
cd frontend && npm run build
```

说明 Phase 4 如何从“能生成”升级为“可观测、可校验、可交互”。
