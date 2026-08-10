Use case: infographic-diagram
Create a clean professional 16:9 system architecture infographic titled "Sage Agent Harness × Agentic RAG 总体架构" with subtitle "可约束 · 可恢复 · 可评测".
Audience: AI application engineers. White background, navy/teal/amber/red/purple balanced palette, crisp readable Chinese and English typography, thin arrows, four horizontal lanes, generous whitespace, no tiny text, no gradients, no 3D, no watermark.

Lane 1: "输入与控制面". Show this exact left-to-right flow: "用户 Turn" -> "TaskIntent admission" -> "LearningIntent route" -> "Retrieval Gate" -> "TurnContextPlan + ToolBundle". Add note: "意图只收窄候选，不授予权限".

Lane 2: "Harness 运行编排". Show: "Harness Graph" -> "Model / Tool Loop" -> "Task DAG / Subagent" -> "Checkpoint" -> "Resume" -> back to "Harness Graph". Add small labels: "Timeline", "Trace", "Artifact", "lease / fencing", "服务端校验 · 预算预约 · ready wave".

Lane 3: "PostgreSQL Agentic RAG". Show: "SQLite canonical truth" -> "PostgreSQL search projection" -> branches "GIN + ts_rank_cd sparse" and "pgvector exact cosine dense" -> "RRF fusion" -> "EvidenceBundle + citation" -> diamond "Claim Sufficiency". Green branch: "证据足够" -> "Answer Gate" -> "最终答案 + citation". Amber branch: "缺 Claim" -> "bounded Research child" -> back to EvidenceBundle. Red branch: "仍不足" -> "诚实拒答". Dashed note: "Query Rewrite / HNSW / BM25：未过门禁不默认启用".

Lane 4: "安全与评测护栏". Show a control rail: "Schema / Path" -> "Permission" -> "Policy" -> "Approval" -> "Container Sandbox". Show a second evaluation rail: "Intent" -> "Retrieval" -> "Claim" -> "Generation" -> "Recovery" -> "Provider / P95". Add four KPI callouts: "Claim Evidence Coverage", "Answer Correctness", "Correct Abstention / False Acceptance", "Claim Recovery Gain".

Render all provided labels exactly once, preserve English identifiers, no extra components, no overlapping text, no cropped nodes.
