# Sage Agent Harness × Agentic RAG 总体架构图生成提示词

```text
Use case: infographic-diagram
Asset type: professional system architecture infographic
Primary request: create a complete 16:9 architecture diagram titled "Sage Agent Harness × Agentic RAG 总体架构"
Subtitle (verbatim): "可约束 · 可恢复 · 可评测"

Audience: AI application engineers and technical interviewers.
Style/medium: clean modern engineering architecture diagram, vector-like, white or very light gray background, crisp sans-serif Chinese typography, thin directional arrows, restrained professional colors. No hand-drawn style, no 3D, no decorative gradients.
Composition/framing: 2048x1152 landscape, four clearly separated horizontal lanes, left-to-right primary flow, generous whitespace, no tiny labels, no crossed arrows. Use rounded rectangles with radius no more than 8px.

Lane 1 title (verbatim): "1 输入与控制面"
Primary flow labels (verbatim):
"用户 Turn" -> "TaskIntent admission" -> "LearningIntent route" -> "Retrieval Gate" -> "TurnContextPlan + ToolBundle"
Add a small boundary note (verbatim): "意图只收窄候选，不授予权限"

Lane 2 title (verbatim): "2 Harness 运行编排"
Primary flow labels (verbatim):
"Harness Graph" -> "Model / Tool Loop" -> "Task DAG / Subagent" -> "Checkpoint"
Under "Task DAG / Subagent", add compact text (verbatim): "服务端校验 · 预算预约 · ready wave"
From "Checkpoint" lead to "Resume" and then back to "Harness Graph".
Add side labels (verbatim): "Timeline", "Trace", "Artifact", "lease / fencing".

Lane 3 title (verbatim): "3 PostgreSQL Agentic RAG"
Flow labels (verbatim):
"SQLite canonical truth" -> "PostgreSQL search projection"
From "PostgreSQL search projection" branch into:
"GIN + ts_rank_cd sparse"
"pgvector exact cosine dense"
Merge both into "RRF fusion" -> "EvidenceBundle + citation" -> diamond "Claim Sufficiency"
From diamond:
green arrow "证据足够" -> "Answer Gate" -> "最终答案 + citation"
orange arrow "缺 Claim" -> "bounded Research child" -> back to "EvidenceBundle + citation"
red arrow "仍不足" -> "诚实拒答"
Add a small dashed candidate box (verbatim): "Query Rewrite / HNSW / BM25：未过门禁不默认启用"

Lane 4 title (verbatim): "4 安全与评测护栏"
Security rail labels (verbatim):
"Schema / Path" -> "Permission" -> "Policy" -> "Approval" -> "Container Sandbox"
Under Sandbox add compact text (verbatim): "seccomp · cap-drop ALL · no-new-privileges · read-only rootfs · network none · CPU/RAM/PID"
Evaluation rail labels (verbatim):
"Intent" -> "Retrieval" -> "Claim" -> "Generation" -> "Recovery" -> "Provider / P95"
Add four KPI callouts (verbatim):
"Claim Evidence Coverage"
"Answer Correctness"
"Correct Abstention / False Acceptance"
"Claim Recovery Gain"

Color system: navy for primary flow, teal/green for validated success, amber for conditional recovery and candidates, red only for fail-closed/abstention, purple for evidence and evaluation. Keep colors balanced; avoid a single-color palette.
Constraints: render every supplied label exactly once; Chinese must be legible and correctly spelled; preserve English identifiers exactly; no extra components, no logos, no watermark, no fake metrics, no dark background, no overlapping text, no cropped nodes.
```
