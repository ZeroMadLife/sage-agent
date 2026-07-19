import type {
  KnowledgeGoalAlignment,
  KnowledgeGraphNeighborhood,
  KnowledgeGraphNode,
  KnowledgePage,
} from '../types/api'

export type KnowledgeNodeResearchIntent = 'understand' | 'evidence' | 'practice'

export type KnowledgeNodeResearchModel = {
  nodeId: string
  label: string
  graphRevision: string | null
  pageRevision: string | null
  sourceRevision: string | null
  directConnectionCount: number | null
  goalCapability: string | null
  evidenceBound: boolean
}

export function buildKnowledgeNodeResearchModel(input: {
  node: KnowledgeGraphNode
  graphRevision?: string | null
  neighborhood?: KnowledgeGraphNeighborhood | null
  alignments?: KnowledgeGoalAlignment[]
}): KnowledgeNodeResearchModel {
  const goalAlignment = input.alignments?.find(
    (item) => item.matched_node_ids.includes(input.node.node_id),
  )
  return {
    nodeId: input.node.node_id,
    label: input.node.label,
    graphRevision: input.graphRevision ?? null,
    pageRevision: input.node.page_revision,
    sourceRevision: input.node.source_revision,
    directConnectionCount: input.neighborhood?.edges.length ?? null,
    goalCapability: goalAlignment?.label ?? null,
    evidenceBound: Boolean(input.node.page_revision || input.node.source_revision),
  }
}

export function buildKnowledgeNodeResearchPrompt(
  intent: KnowledgeNodeResearchIntent,
  model: KnowledgeNodeResearchModel,
): string {
  const boundary = model.evidenceBound
    ? '当前提交会携带已选节点及可用的 page/source revision。'
    : '当前节点只有 graph revision；请把缺少 page/source revision 视为证据缺口，不要把关系推断写成事实。'

  if (intent === 'evidence') {
    return `请为已选知识节点「${model.label}」草拟一份补证计划。\n\n${boundary}\n\n请先核对提交时冻结的 Knowledge surface_context，并列出：\n1. 当前已有证据可以支持什么；\n2. 仍缺少哪些关键证据；\n3. 拟搜索的主题、查询词、来源类型与预期可信度；\n4. 哪些内容适合使用现有 RAG，哪些需要 Web/MCP。\n\n先等待我确认计划，再使用 Web/MCP。外部来源需要保存时只创建待审批 proposal，不要直接写入 Wiki、Memory 或更新掌握度。`
  }

  if (intent === 'practice') {
    return `请围绕已选知识节点「${model.label}」设计一次可验证练习。\n\n${boundary}\n\n请先使用提交时冻结的 Knowledge surface_context 核对已有证据，然后给出：\n1. 一个明确的练习目标；\n2. 分步任务与必要约束；\n3. 可客观判断的通过标准；\n4. 完成后应记录的证据，以及仍待验证的薄弱点。\n\n如果现有 revision 不足以支撑练习，请先指出证据缺口。不要自动更新掌握度、Wiki 或长期 Memory。`
  }

  return `请围绕已选知识节点「${model.label}」做一次概念梳理。\n\n${boundary}\n\n请先只使用提交时冻结的 Knowledge surface_context 检索已有证据，并完成：\n1. 解释这个学习点的核心概念、边界与常见误区；\n2. 说明它与一跳邻域中关键节点的关系；\n3. 区分已有证据、合理推断和仍缺少的证据；\n4. 为关键结论附上可追溯引用。\n\n若现有 revision 证据不足，先提出补证计划。不要自动写入 Wiki、Memory 或更新掌握度。`
}

export function buildKnowledgePageResearchPrompt(page: KnowledgePage): string {
  return `请围绕已选 Wiki 页面「${page.title}」继续研究。\n\n当前提交会携带 page ${page.page_id} 与 revision ${page.current_revision} 的 Knowledge surface_context。\n\n请先只检索该冻结 revision 及其已有引用，并完成：\n1. 提炼页面覆盖的核心问题与当前结论；\n2. 区分可追溯事实、合理推断和仍缺少的证据；\n3. 说明它与当前学习目标的关系；\n4. 给出最值得继续追问或实践验证的下一步。\n\n若现有 revision 证据不足，先提出补证计划。不要自动写入 Wiki、Memory 或更新掌握度。`
}
