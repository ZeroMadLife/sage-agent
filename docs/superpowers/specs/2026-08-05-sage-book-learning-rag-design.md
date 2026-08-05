# Sage 学习书架与成长型 RAG 设计

> 日期：2026-08-05
> 基线：`dev/sage-v7@53a6a3c`
> 状态：设计草案，待用户审阅；不代表已实现能力
> 目标版本：`book-learning-v1`

## 1. 产品结论

Sage 不新增一个“书籍 RAG 搜索器”，而是新增一个面向成长目标的 **学习书架
（Learning Shelf）**。书是高密度、可追溯的知识来源；Sage 负责从用户的学习问题出发，
自动选择合适的书和章节、组织证据、给出答案，并在用户愿意时提供一个很短的验证动作。

用户不需要先挑书。主页面仍然是一个极简对话入口：

```text
用户：我想系统学习金融，先从投资基础开始
  -> Sage 创建/更新 Learning Goal
  -> 后台从书架匹配 Book Profile 和 Chapter Profile
  -> 主对话返回当前答案与学习路径
  -> “正在参考：2 本书 / 3 个章节”可展开查看
  -> 可选微验证；只有真实完成的验证才形成成长证据
```

书籍选择是默认的软路由，而不是一次性的用户决策。用户可以查看、替换或固定来源，
但“没有选择书”不应阻塞学习。只有来源不可访问、互相冲突、版权状态不明或问题需要
用户澄清时，才将选择权显式交还给用户。

原始书籍 revision 是 canonical evidence。章节摘要、概念图、embedding、Skill 和 Agent
产物都是带版本的可重建投影，不能覆盖原文，也不能在没有来源引用时升级为长期事实。

## 2. 现状与设计边界

### 2.1 当前可复用能力

- Knowledge 已有 source revision、proposal-first、稳定 citation、SQLite/PostgreSQL
  混合检索和 citation-bound 一跳关系检索。
- Chat Harness 已是主对话、Knowledge 和 Practice 的共享运行壳；timeline 是运行事实的
  投影，不另建聊天运行时。
- Subagent 服务端已有 `research`、`synthesize`、`practice` profile，并限制最大并发
  3、单次 run 最多 6 个子 Agent、委派深度 1。
- `Synthesize` 没有服务器授权的 EvidenceBundle 时会 fail closed，Research 子 Agent 是
  只读能力，Practice 子 Agent 才能执行练习工具。
- Cangjie Skill 已包含章节、trigger、反场景、边界和压力测试，可作为方法执行层与路由
  评测素材。

### 2.2 当前缺口

- 解析层支持 Markdown、HTML、PDF、DOCX、PNG，尚不支持 TXT/EPUB；`ParsedBlock` 尚未保存
  行号、字符区间或字节区间。
- `Retrieval Gate` 主要依靠显式关键词，普通的“解释金融概念”问题不会稳定触发 Knowledge。
- 关系检索目前是带 citation 的显式 `WIKILINK` 一跳，不是无限制的 GraphRAG。
- 书籍章节、学习目标、路由 receipt 和成长证据还没有统一的领域模型。

本设计解决产品契约和可验证的技术切片；不在规格阶段宣称准确率提升，不把研究候选写成
线上能力，也不在用户批准前修改实现代码。

## 3. 产品信息架构

### 3.1 学习书架

书架是用户看见的来源集合，按“学习目标”自动组织，而不是按检索技术展示。每本书有：

- 标题、作者、语言、主题标签和来源类型；
- `source_id`、当前 active revision、导入时间和 parser 状态；
- 版权/来源声明（本地用户拥有、公共领域、用户自行确认等）；
- 适合的学习阶段、难度、前置概念和适用问题类型；
- 当前可用的章节结构、概念索引、引用覆盖率和解析置信度；
- 可选的用户偏好：固定、降权、暂不使用。

首页不展示“请选择一本书”的必填卡片。可以在学习目标下方展示紧凑的来源摘要：

```text
学习目标：金融基础
当前路径：货币 -> 利率 -> 风险与收益
参考来源：金融学导论（第 2、3 章）· 投资学基础（第 1 章）
```

来源摘要默认可折叠，展开后才显示命中的章节、引用和“换一组来源”操作。这样保持主
对话简单，又让系统的证据选择可检查。

### 3.2 自动软路由

每一轮问题都计算一个短生命周期的 `BookContext`，而不是永久修改用户的书架：

```text
LearningGoal
  -> intent route
  -> candidate BookProfile[]
  -> candidate ChapterProfile[]
  -> retrieval route
  -> EvidenceBundle
  -> answer + optional verification
```

用户明确说“只根据某书”或固定某来源时，显式约束优先于自动推荐；用户说“不要联网”时，
路由不能选择 Web。找不到足够证据时，Sage 先诚实说明缺口，再建议添加或固定来源，
不能用书架中相似但无关的文本填满答案。

## 4. 领域模型

所有 ID 都是不可变、可审计的字符串；所有派生对象都绑定输入 revision 和生成器 revision。
以下是领域契约，不要求一次性按原样落数据库。

### 4.1 `LearningGoal`

```text
goal_id, owner_id, workspace_id,
title, user_intent, domain,
stage: discover | understand | apply | assess,
target_outcomes[], prerequisite_goals[],
active_shelf_id, status: active | paused | completed,
created_at, updated_at
```

`stage` 描述学习行为，不代表模型判断用户已经掌握。目标完成必须由 Practice 或闭卷验证
产生的证据支持，不能由对话轮数或模型自评分推断。

### 4.2 `Shelf`

```text
shelf_id, owner_id, workspace_id,
name, goal_ids[], book_ids[],
selection_policy: auto | pinned | user_only,
created_at, updated_at
```

`auto` 是默认策略。`pinned` 表示用户要求保留某些来源，但仍可在证据不足时报告缺口。

### 4.3 `BookProfile`

```text
book_id, shelf_id, source_id,
title, authors[], language, topics[],
source_revision, parser_provenance,
structure_status: structured | proposed | windowed | failed,
trust_status: user_owned | public_domain | user_confirmed | unknown,
chapter_ids[], concept_ids[],
fit_signals: difficulty, stage_fit, topic_fit, citation_coverage,
selection_weight, status: active | paused | retired
```

`selection_weight` 只能影响候选排序，不能绕过 visibility、trust、revision 或 relevance gate。

### 4.4 `ChapterProfile`

```text
chapter_id, book_id, source_revision,
path: [part, chapter, section...], title, ordinal,
summary, key_concepts[], prerequisite_concepts[],
block_ids[], parent_chunk_id, child_chunk_ids[],
profile_provenance, confidence
```

摘要和概念是 derived projection；回答引用必须落到原始 block 的 `CitationSpan`，而不是
只引用章节摘要。

### 4.5 解析与引用对象

建议扩展 `ParsedBlock`：

```text
block_id, ordinal, kind, text, heading_path,
page?, bbox?, media_ref?, confidence?,
line_start?, line_end?,
char_start?, char_end?,
byte_start?, byte_end?,
locator: epub_item_id?, epub_href?, fragment?,
content_hash, source_revision
```

`CitationSpan` 至少包含：

```text
citation_id, source_id, source_revision, block_id,
locator_type: page | line | offset | epub_fragment | bbox,
locator, excerpt_hash, heading_path
```

TXT 不伪造页码。回答显示“第 3 章，行 182-205”或“字符 offset 12540-13290”，并保留
content hash 以便 revision 变化后检测过期引用。

### 4.6 `EvidenceBundle`

```text
bundle_id, parent_run_id, query_fingerprint,
evidence[]: {citation_id, text, source_role, score, hop, relation_path[]},
book_context[], conflicts[], missing[],
retrieval_receipt, token_budget, created_at, expires_at
```

`EvidenceBundle` 由服务端组装并签名/绑定 workspace、source revision 和预算。Synthesize
子 Agent 只能读取它，不能自行搜索、写入 Knowledge 或扩大权限。最终答案必须引用 bundle
中的 citation；无法引用的内容应标为推断、常识或未知。

### 4.7 学习证据

```text
LearningEvidenceCandidate {
  evidence_id, goal_id, run_id, kind,
  prompt, user_response?, expected_receipt,
  citations[], score?, rubric_revision,
  status: candidate | accepted | rejected,
  created_at
}
```

证据类型包括 `explain`、`compare`、`predict`、`apply`、`closed_book_recall`。阅读次数、
停留时间、点赞和模型“我认为你掌握了”都不能单独形成 Mastery Evidence。

## 5. 意图识别与小模型路线

### 5.1 路由输出

小模型只负责结构化分类和置信度，不负责回答事实问题。输出必须通过 schema 校验：

```text
IntentRoute {
  intent_family: explain | compare | plan | practice | recall | research | meta,
  knowledge_scope: none | workspace | book | web | mixed,
  candidate_topics[], candidate_book_ids[], candidate_chapter_ids[],
  depth: direct | multi_hop,
  learning_stage: discover | understand | apply | assess,
  risk_flags[], confidence, abstain_reason?
}
```

硬约束先处理“只用某来源”“不要联网”“最新资料”等显式表达；随后由小模型判断意图、
主题和多跳倾向；最后结合书架 embedding/metadata 选择候选书和章节。任意阶段出现冲突，
以显式用户约束和服务端 Policy 为准。

### 5.2 生产路由顺序

```text
显式约束/安全策略
  -> 规则基线（低延迟、可解释）
  -> 小模型结构化分类
  -> 书架 metadata + embedding 候选
  -> confidence / disagreement gate
      -> high confidence: 直接走目标 route
      -> low confidence: 扩大候选并由大模型作一次路由裁决
      -> unavailable/unsafe: fail closed 或请求最小澄清
```

用户不需要为低置信度路由承担选择负担。系统优先扩大检索或交给大模型裁决；只有多个
来源互相矛盾、权限/版权不清或问题本身不完整时才提问。

### 5.3 SFT 训练模块

第一阶段先建立规则 + embedding + 小模型的可解释 baseline，再积累标注。训练样本来源：

- 脱敏后的真实问题与人工修正 route；
- Cangjie Skill 的 `should_trigger`、`should_not_trigger`、跨 Skill 混淆和压力测试；
- 现有 RAG benchmark 的 source scope、answerability、relation_kind 和 hard negative；
- 合成改写必须保留 `leakage_group`，不得跨 dev/test。

SFT 的 target 是 `IntentRoute`，不是答案文本。每个模型 revision 绑定 dataset revision、
label schema、prompt/schema version 和评测报告。训练不改变 canonical book content。

### 5.4 RL 只进入离线 Algorithm Lab

RL 暂不进入生产闭环。它需要先有稳定的可观测奖励：

```text
reward = retrieval_quality
       - cost_penalty
       - latency_penalty
       - false_acceptance_penalty
       - unnecessary_agent_penalty
```

离线实验比较规则、SFT 和 RL policy 的 route accuracy、abstain F1、answerable recall、
citation precision、P95、token cost 和错误接受率。不得用用户是否继续聊天、点击次数或
单一 LLM judge 作为唯一 reward，也不得让在线 RL 自动改变生产路由；发布仍需要冻结模型、
数据集和 gate 配置，并通过回归集。

## 6. Leader + 多 Agent 工作流

### 6.1 角色边界

```text
Leader/父 Agent
  -> 解释问题、规划预算、决定是否升级 Agentic RAG
  -> 并行委派 Research children
  -> 合并结果、处理冲突、决定停止
  -> 生成最终答案和可选微验证

Research child（按书/章节分工）
  -> 只读 Knowledge/Web/工作区范围内的授权检索
  -> 返回 evidence-backed brief、citation、缺口和冲突

Synthesize child
  -> 只读取服务器授权 EvidenceBundle
  -> 只做证据内综合，不重新搜索或写入

Practice child
  -> 只执行具体练习和确定性验证
  -> 只能产生 Mastery Evidence candidate，不能宣称掌握
```

Leader 复用现有 Subagent runtime。Research children 可以按候选书或章节并行，但不得按
chunk 创建 Agent；不得递归委派。默认并行数、单 run 数和深度继续使用服务端现有上限，
book-learning 只能收紧，不能放宽。

### 6.2 何时升级 Agentic RAG

| 条件 | 路径 |
| --- | --- |
| 单一概念、证据充分、无需比较 | Hybrid RAG，一次检索和回答 |
| 多本书比较、跨章节组合、证据冲突 | Leader + 并行 Research + EvidenceBundle |
| 关系问题或需要多跳 | 先 relevance gate，再有界关系扩展；必要时 Agentic |
| 证据不足/无答案 | 先扩大候选一次；仍不足则拒答或请求最小澄清 |
| 需要练习和验收 | 回答后生成可选 Practice/Recall 子任务 |

Agentic 不是默认的“更强模型模式”，而是一个有预算的恢复策略。每次升级必须记录
`route_reason`、child 数、检索次数、token、延迟和停止原因。

### 6.3 EvidenceBundle 合并与停止

Leader 只能合并带 citation 的 child brief。合并步骤为：去重相同 citation、保留来源冲突、
按 source revision 与 hop 归一化分数、检查 required evidence 是否齐全，然后在以下任一条件
满足时停止：

- 已覆盖问题所需的全部 evidence claims；
- 连续一次扩展没有新增 required claim；
- 达到 child、节点、token 或延迟预算；
- relevance gate 判定证据不足且扩大候选也无改善。

超时或 child 失败不会被静默视为“没有冲突”。答案必须带有限制说明，并可由 timeline 恢复。

## 7. TXT、EPUB 与不规则电子书解析

### 7.1 不可变原件与版本映射

摄取前保存原始 bytes、`source_revision=sha256(raw)`、媒体类型和文件大小；解析时保存
`parser_id/parser_version/encoding/normalization_revision`。规范化文本必须带 raw-to-normalized
映射，避免换行、Unicode 规范化或 BOM 处理后无法定位原文。

所有解析结果都绑定 source revision。解析失败或低置信 proposal 只能停留在 draft，不能
替换上一个 active projection。

### 7.2 TXT

1. 按 BOM、UTF-8、UTF-16 和受控的 GB18030 fallback 检测编码；记录检测结果，不悄悄改写原件。
2. 保留 `line_start/line_end`、`char_start/char_end` 和 `byte_start/byte_end`；换行符差异
   通过映射表处理。
3. 先用确定性规则识别标题：连续空行、短行、编号（如“第 1 章”“1.2”）、目录项、全角
   标点和章节关键词。规则不能确定时，保留平铺 block 并标记 `structure_status=windowed`。
4. 不规则纯文本使用带 overlap 的窗口切分，窗口必须有 ordinal、边界映射和 content hash；
   不能为了生成漂亮章节而丢弃无法归类的正文。

### 7.3 EPUB

1. 以 ZIP 安全检查为前置：文件数量、单文件大小、总解压大小、路径穿越和压缩炸弹都受限。
2. 读取 `container.xml`、`content.opf` 和 spine，按阅读顺序处理 XHTML；保留 `epub_item_id`、
   href 和 fragment locator。
3. 解析 HTML heading/section、段落、列表、表格和代码；CSS 仅作为显示信息，不成为事实源。
4. 目录和 spine 只能提出章节候选，若正文结构与目录冲突，生成低置信 `structure proposal`
   并报告冲突；不得静默覆盖原始顺序。

### 7.4 层级索引

统一层级为：

```text
Book -> Part -> Chapter -> Section -> Block
```

检索先召回 child block，再补充 parent section/chapter 摘要；回答引用仍指向 child block。
章节摘要、前置概念和实体关系异步生成，任何一项失败都不阻塞原文 block 的可检索性。

### 7.5 解析评测

建立带人工 gold 的 TXT/EPUB fixture：正常章节、无标题散文、目录混入正文、编码异常、
长段落、重复页眉页脚、脚注和 EPUB 跨文件章节。评测结构覆盖率、正文保留率、边界 F1、
引用 locator 正确率、raw-to-normalized 映射和重复内容率。无结构文本以“内容不丢、引用可回溯”
为通过条件，不以模型猜出漂亮目录为通过条件。

## 8. Parent-Child Chunking 与有界关系 RAG

### 8.1 Parent-Child

`parent_chunk` 保存 section/chapter 级上下文，`child_chunk` 保存可引用的证据片段。child
优先按结构边界切分，超长 block 才按 token 上限切分；每个 child 继承完整 heading path、
source revision 和 locator。组装 EvidenceBundle 时先用 child 命中，再在总 token 预算内补一份
parent context，避免把整章塞进 prompt。

### 8.2 有界扩展

关系扩展严格发生在 seed retrieval 和 relevance gate 之后：

```text
sparse + dense seed
  -> absolute relevance gate
  -> allowed evidence edges
  -> bounded 1-hop default / 2-hop exceptional
  -> dedupe + score decay
  -> token-bounded EvidenceBundle
```

每个 graph candidate 必须保留 seed citation、edge evidence、target revision 和 relation path。
建议初始策略（最终阈值仅由 dev split 校准）：

- seed 不超过 4 个；每个 seed 邻居不超过 6 个；
- 默认最多 1 hop，只有 `depth=multi_hop` 且第一跳新增 required claim 时才允许第二跳；
- 总节点不超过 24，关系候选不超过 48，EvidenceBundle 不超过 6,000 tokens；
- hop 衰减 `score * 0.65^hop`，同一 citation/claim 去重；
- 连续一轮没有新增 required claim、路径无 edge evidence 或候选 margin 低于 gate 时停止。

不使用没有证据的 `SHARES_SOURCE` 或可视化 community 作为相关性传播边。完整实体三元组、
PPR 和 community summary 是后续候选，只有 relation benchmark 证明一跳不足时才引入。

## 9. Cangjie Skill 集成

Cangjie Skill 作为“方法执行层”导入 Sage：它可以提供学习步骤、触发条件、反场景、边界和
练习模板，但不覆盖原书 citation，不自动修改 BookProfile，也不升级为长期 Memory。

导入时保存 `skill_id/skill_revision/trigger_rules/anti_trigger_rules`。`should_trigger`、
`should_not_trigger`、跨 Skill 混淆和压力测试转成路由 benchmark；Skill 触发只产生一个受限
method context，最终事实仍来自 EvidenceBundle 或 Practice receipt。

## 10. 学习回答与成长闭环

回答默认采用“答案优先 + 可跳过微验证”：

1. 先给直接答案、适用范围、引用和不确定性；
2. 末尾最多给一个 1-3 分钟的解释、比较、预测或应用题；
3. 用户跳过只保留对话记录，不形成掌握证据；
4. 用户完成后，Practice/Recall receipt 与引用一起生成 `LearningEvidenceCandidate`；
5. 只有服务端确定性 rubric、测试输出或闭卷答案满足门槛，才可由策略批准为 Mastery Evidence。

系统不以读完一本书、阅读时长、连续签到、模型自评或用户点击“我懂了”判定成长。目标状态
和学习路径可以自动更新为“下一步建议”，但长期事实和掌握等级需要证据。

## 11. 评测合同

新增 `book-learning-v1` 数据集，和现有 passage/relation benchmark 分开版本化。每条 case
至少包含：

```text
case_id, split, leakage_group, query, answerable,
intent_family, knowledge_scope, depth, learning_stage,
gold_book_ids[], gold_chapter_ids[], required_claims[],
required_citations[], gold_relation_paths[],
should_abstain, skill_ids[], forbidden_claims[]
```

### 11.1 路由与小模型

- intent macro/micro F1、book/chapter candidate Recall@K；
- abstain precision/recall/F1、false acceptance、route disagreement；
- 显式约束遵守率（只用某书、禁止联网、workspace/owner 隔离）；
- SFT/RL policy 的 token、P50/P95、模型调用次数和 fallback 比例。

### 11.2 检索、关系与引用

- passage Recall@K、MRR、NDCG@K、multi-hop AllRecall@K；
- citation precision、citation freshness、unsupported claim rate；
- relation path precision、gold path recall、无证据 edge rate；
- no-answer risk-coverage、answerable coverage、拒答准确率；
- 每次扩展的 child/node/hop/token 数、P50/P95 和成本。

### 11.3 Agent 与成长

- Leader 停止条件遵守率、child 越界率、EvidenceBundle 完整率；
- child 失败/超时后的诚实降级率；
- 微验证完成率、确定性评分一致性、Mastery 误接受率；
- 练习 receipt 可重放、workspace/revision 隔离和隐私泄漏测试。

阈值只在 dev/calibration split 选择，test split 只验收。任何首轮数字都必须带 corpus、
dataset、model、parser、embedding 和 commit revision；没有跑过的能力写成“设计目标”。

建议第一轮门禁（待基线实测后冻结）：

| 门禁 | 建议目标 |
| --- | --- |
| 显式来源/联网约束遵守 | 100% |
| 关键 citation 指向当前 revision | 100% |
| parser 正文保留与 locator 正确 | 100% fixture case |
| 未授权 child 工具调用 | 0 |
| 无证据关系边进入答案 | 0 |
| no-answer false acceptance | 不高于冻结 baseline |

## 12. 分阶段交付

### Phase 0：契约与数据集

- 冻结 LearningGoal/Shelf/BookProfile/ChapterProfile/CitationSpan/EvidenceBundle schema；
- 添加 TXT/EPUB fixture 和 `book-learning-v1` route/parser/evidence 数据集；
- 扩展 `ParsedBlock` 的 line/offset/locator，不改变现有 Markdown/PDF 行为；
- 明确版权、路径、大小、ZIP 安全和 source revision 门禁。

### Phase 1：自动书架与 Hybrid RAG

- 接入本地 TXT/EPUB 导入和 `Book -> Chapter -> Block` 层级索引；
- 在现有 Retrieval Gate 之后加入结构化小模型路由和 confidence/abstain；
- 实现主对话自动候选书/章节、parent-child context、稳定 TXT/EPUB citation；
- 首页只显示可折叠的“正在参考”摘要，不增加必选书步骤。

### Phase 2：Agentic RAG 与有界关系扩展

- Leader 复用现有 Subagent runtime；Research 按书/章节并行，Synthesize 只读 EvidenceBundle；
- 加入多跳 route、bounded graph expansion、冲突/缺口合并和可恢复 timeline 事件；
- 通过 relation/agent benchmark 验收后，才考虑 2-hop 或更复杂的实体投影。

### Phase 3：SFT Algorithm Lab 与 Skill

- 用人工 route 和 Cangjie trigger 数据训练第一版 SFT 小模型；
- 对规则、SFT 和候选 RL policy 做离线消融；
- 将 Cangjie Skill 作为 method context 和压力测试接入，不改变事实层。

### Phase 4：微验证与成长证据

- 把解释、应用、闭卷回忆接入 Practice/Recall receipt；
- 建立证据候选、批准/拒绝、复测和目标进度投影；
- 只有离线 reward、评测和安全边界稳定后，才评估 RL policy 的受控灰度。

## 13. 非目标与风险

- 不自动从互联网下载或分发版权不明的整本书；Web 研究结果默认只用于当前回答，保存需走
  现有 source/proposal 门禁。
- 不把所有 chunk 各自交给一个 Agent；不允许子 Agent 递归委派或自行扩大工具权限。
- 不在生产在线训练或自动 RL；不把用户点击和聊天时长当作掌握标签。
- 不在 v1 引入完整 Microsoft GraphRAG、无限图遍历、PPR 或社区报告；图仍是可重建投影。
- 不让小模型直接回答事实；小模型错误时必须可 abstain 并由大模型/扩大检索兜底。
- 大文件、编码异常、扫描 PDF、脚注和跨 EPUB 文件会造成结构低置信；降级策略必须保内容和
  引用，而不是生成虚假的章节或页码。

主要风险是路由误判造成漏召回、关系扩展造成噪声和成本增长、解析映射失真导致 citation
不可复核，以及把“读过”误当成“学会”。每个风险都由 route/evidence/parser/mastery
门禁覆盖；未经对应评测，不得用产品文案夸大为“自动教会用户”。

## 14. 验收与审阅边界

本规格阶段只交付可审阅的产品/技术契约。用户批准后，下一份实施计划必须把每个 Phase 拆成
可独立验证的垂直 slice，并对每个 slice 指定代码入口、测试、数据集和回滚边界。

规格通过的最低条件：

1. 产品默认无需用户选书，但来源选择可查看、可覆盖、可审计；
2. canonical source、derived projection、Skill、Agent brief 和成长证据的责任边界清晰；
3. TXT/EPUB 无结构降级仍不丢内容且 citation 可定位；
4. 小模型有 confidence/abstain，SFT 与 RL 的生产边界明确；
5. Leader、多 Agent、关系扩展都受服务器预算、EvidenceBundle 和停止条件约束；
6. route、parser、retrieval、relation、agent 和 mastery 都有独立 eval seam；
7. 未实现能力、研究候选和正式工程证据没有混写。

## 15. 参考实践（用于取舍，不等于 Sage 已实现）

- LlamaIndex：Router/Selector 与 parent-child retrieval 的分层思路；
- LangGraph：父图/子图、checkpoint 和可恢复多步编排；
- RAGFlow：面向复杂文档的结构化解析、定位和可视化引用；
- Microsoft GraphRAG：local/global search 的区分，但 Sage v1 只采用 evidence-bound local
  expansion；
- KG2RAG、HippoRAG：先语义 seed，再有界图扩展；
- CRAG/RE-RAG：把 relevance evaluation、fallback 和 rerank 分开；
- Open WebUI/ReadAny：将长文档阅读投影为可追溯上下文，而不是把整本书一次性放入 prompt。

这些实践被约束在 Sage 已有的 revision、workspace、citation、proposal 和 Harness 边界内，
不会因为引入名词就自动获得对应论文或项目的效果。
