<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ArrowUpRight, BookOpenText, ChevronDown, FileText, Link2, LoaderCircle, Network, Target, X } from 'lucide-vue-next'
import { fetchKnowledgeCitation } from '../../api/knowledge'
import { fetchKnowledgePage } from '../../api/knowledge'
import { useMarkdown } from '../../composables/useMarkdown'
import type {
  KnowledgeCitation,
  KnowledgeGoalAlignment,
  KnowledgeGraphCommunity,
  KnowledgeGraphInsight,
  KnowledgeGraphNeighborhood,
  KnowledgeGraphNode,
  KnowledgeLearningGoal,
  KnowledgePage,
  KnowledgePageDocument,
} from '../../types/api'

const props = defineProps<{
  node: KnowledgeGraphNode | null
  page: KnowledgePage | null
  neighborhood: KnowledgeGraphNeighborhood | null
  insights: KnowledgeGraphInsight[]
  goal: KnowledgeLearningGoal | null
  alignments: KnowledgeGoalAlignment[]
  communities: KnowledgeGraphCommunity[]
  communityId: string | null
  loading: boolean
  compact: boolean
}>()

const emit = defineEmits<{
  close: []
  select: [nodeId: string]
}>()

const panel = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const tab = ref<'overview' | 'content' | 'evidence' | 'relations'>('overview')
const pageDocument = ref<KnowledgePageDocument | null>(null)
const pageLoading = ref(false)
const pageError = ref('')
const expandedEvidenceId = ref<string | null>(null)
const evidenceDetails = ref<Record<string, KnowledgeCitation>>({})
const evidenceLoading = ref<Set<string>>(new Set())
const evidenceErrors = ref<Record<string, string>>({})
let evidenceRequestVersion = 0
let pageRequestVersion = 0
const { render } = useMarkdown()
watch([() => props.node?.node_id, () => props.page?.page_id], () => {
  tab.value = 'overview'
  pageDocument.value = null
  pageLoading.value = false
  pageError.value = ''
  pageRequestVersion += 1
  expandedEvidenceId.value = null
  evidenceDetails.value = {}
  evidenceLoading.value = new Set()
  evidenceErrors.value = {}
  evidenceRequestVersion += 1
})

async function selectTab(nextTab: 'overview' | 'content' | 'evidence' | 'relations') {
  tab.value = nextTab
  if (nextTab !== 'content' || !props.page || pageDocument.value || pageLoading.value) return

  const requestVersion = pageRequestVersion
  pageLoading.value = true
  pageError.value = ''
  try {
    const document = await fetchKnowledgePage(props.page.page_id)
    if (requestVersion === pageRequestVersion) pageDocument.value = document
  } catch (reason) {
    if (requestVersion === pageRequestVersion) {
      pageError.value = reason instanceof Error ? reason.message : String(reason)
    }
  } finally {
    if (requestVersion === pageRequestVersion) pageLoading.value = false
  }
}

const community = computed(() => props.communities.find(
  (item) => item.community_id === props.communityId,
) ?? null)
const nodeInsights = computed(() => props.node
  ? props.insights.filter((item) => item.node_id === props.node?.node_id)
  : [])
const neighborNodes = computed(() => props.neighborhood?.nodes.filter(
  (item) => item.node_id !== props.node?.node_id,
) ?? [])
const kindLabels = {
  page: 'Wiki 页面',
  source: '原始来源',
  project: '项目',
  concept: '概念',
  decision: '决策',
  tool: '工具',
} as const
const relationLabels = {
  WIKILINK: 'Wiki 引用',
  EVIDENCED_BY: '来源证据',
  SHARES_SOURCE: '共享来源',
} as const
const sourceDetails = computed(() => {
  if (props.node?.kind !== 'source') return null
  const relativePath = props.node.properties.relative_path
  const sourceKind = props.node.properties.source_kind
  return {
    relativePath: typeof relativePath === 'string' ? relativePath : props.node.label,
    sourceKind: typeof sourceKind === 'string' ? sourceKind : 'local',
  }
})
const relationRows = computed(() => {
  if (!props.node || !props.neighborhood) return []
  const edgeByNeighbor = new Map(props.neighborhood.edges.map((edge) => {
    const neighborId = edge.source_node_id === props.node?.node_id
      ? edge.target_node_id
      : edge.source_node_id
    return [neighborId, edge]
  }))
  return neighborNodes.value.map((neighbor) => ({
    neighbor,
    edge: edgeByNeighbor.get(neighbor.node_id) ?? null,
  }))
})
const evidence = computed(() => {
  const seen = new Set<string>()
  const nodes = props.neighborhood?.nodes ?? []
  return (props.neighborhood?.edges ?? []).flatMap((edge) => edge.evidence.map((item) => {
    const sourceNode = nodes.find((node) => node.source_id === item.source_id && node.kind === 'source')
    const relativePath = sourceNode?.properties.relative_path
    return {
      ...item,
      relation: relationLabels[edge.kind],
      sourceLabel: sourceNode?.label || '来源文档',
      sourcePath: typeof relativePath === 'string' ? relativePath : sourceNode?.label || item.source_id,
    }
  })).filter((item) => {
    if (seen.has(item.citation_id)) return false
    seen.add(item.citation_id)
    return true
  })
})

async function toggleEvidence(citationId: string) {
  if (expandedEvidenceId.value === citationId) {
    expandedEvidenceId.value = null
    return
  }
  expandedEvidenceId.value = citationId
  if (evidenceDetails.value[citationId] || evidenceLoading.value.has(citationId)) return

  const requestVersion = evidenceRequestVersion
  evidenceLoading.value = new Set([...evidenceLoading.value, citationId])
  const nextErrors = { ...evidenceErrors.value }
  delete nextErrors[citationId]
  evidenceErrors.value = nextErrors
  try {
    const detail = await fetchKnowledgeCitation(citationId)
    if (requestVersion !== evidenceRequestVersion) return
    evidenceDetails.value = { ...evidenceDetails.value, [citationId]: detail }
  } catch (reason) {
    if (requestVersion !== evidenceRequestVersion) return
    evidenceErrors.value = {
      ...evidenceErrors.value,
      [citationId]: reason instanceof Error ? reason.message : String(reason),
    }
  } finally {
    if (requestVersion === evidenceRequestVersion) {
      const nextLoading = new Set(evidenceLoading.value)
      nextLoading.delete(citationId)
      evidenceLoading.value = nextLoading
    }
  }
}

function focusableElements() {
  if (!panel.value) return []
  return [...panel.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )]
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.compact) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab') return
  const elements = focusableElements()
  if (!elements.length) return
  const index = elements.indexOf(document.activeElement as HTMLElement)
  const next = event.shiftKey ? index - 1 : index + 1
  if ((!event.shiftKey && (index < 0 || next >= elements.length)) || (event.shiftKey && index <= 0)) {
    event.preventDefault()
    elements[event.shiftKey ? elements.length - 1 : 0]?.focus()
  }
}

onMounted(() => {
  if (props.compact) void nextTick(() => closeButton.value?.focus())
})
</script>

<template>
  <aside
    ref="panel"
    class="knowledge-inspector"
    :class="{ compact }"
    :role="compact ? 'dialog' : undefined"
    :aria-modal="compact ? 'true' : undefined"
    :aria-label="compact ? '知识详情' : '知识详情面板'"
    @keydown.capture="handleKeydown"
  >
    <header class="inspector-heading">
      <div>
        <span>{{ node ? kindLabels[node.kind] : page ? 'Wiki 页面' : '学习目标' }}</span>
        <h2>{{ node?.label || page?.title || goal?.title || '本地知识分析' }}</h2>
      </div>
      <div class="inspector-heading-actions">
        <button ref="closeButton" type="button" aria-label="关闭知识详情" title="关闭" @click="emit('close')">
          <X :size="18" />
        </button>
      </div>
    </header>

    <template v-if="node || page">
      <nav
        class="inspector-tabs"
        :class="{ 'has-content': page && node, 'page-only': page && !node }"
        aria-label="详情分类"
      >
        <button type="button" :class="{ active: tab === 'overview' }" @click="selectTab('overview')">概览</button>
        <button v-if="page" type="button" :class="{ active: tab === 'content' }" @click="selectTab('content')">正文</button>
        <button v-if="node" type="button" :class="{ active: tab === 'evidence' }" @click="selectTab('evidence')">证据 {{ evidence.length }}</button>
        <button v-if="node" type="button" :class="{ active: tab === 'relations' }" @click="selectTab('relations')">连接 {{ neighborhood?.edges.length ?? 0 }}</button>
      </nav>

      <div v-if="loading" class="inspector-state" role="status">正在读取节点详情...</div>
      <div v-else-if="tab === 'overview'" class="inspector-body">
        <section v-if="sourceDetails" aria-labelledby="source-title">
          <div class="section-title"><FileText :size="16" /><h3 id="source-title">原始来源</h3></div>
          <strong>{{ sourceDetails.relativePath }}</strong>
          <p>{{ sourceDetails.sourceKind }} 来源只读保留；知识页、索引与图谱均可从该版本重建。</p>
        </section>
        <section v-if="community" aria-labelledby="community-title">
          <div class="section-title"><Network :size="16" /><h3 id="community-title">所属社区</h3></div>
          <strong>{{ community.label }}</strong>
          <p>{{ community.node_count }} 个节点 · {{ community.edge_count }} 条内部边 · 紧密度 {{ community.cohesion.toFixed(2) }}</p>
        </section>
        <section v-if="page" aria-labelledby="page-title">
          <div class="section-title"><BookOpenText :size="16" /><h3 id="page-title">Wiki 页面</h3></div>
          <strong>{{ page.title }}</strong>
          <p>当前版本已绑定 · 共 {{ page.revisions.length }} 个历史版本</p>
        </section>
        <section v-if="nodeInsights.length" aria-labelledby="insight-title">
          <div class="section-title"><Target :size="16" /><h3 id="insight-title">本地洞察</h3></div>
          <article v-for="insight in nodeInsights" :key="insight.insight_id" :data-severity="insight.severity">
            <strong>{{ insight.title }}</strong><p>{{ insight.description }}</p>
          </article>
        </section>
        <section v-if="node" aria-labelledby="revision-title">
          <div class="section-title"><Link2 :size="16" /><h3 id="revision-title">版本绑定</h3></div>
          <dl>
            <div><dt>知识页</dt><dd>{{ node.page_revision ? '已绑定' : '未绑定' }}</dd></div>
            <div><dt>来源快照</dt><dd>{{ node.source_revision ? '已绑定' : '未绑定' }}</dd></div>
          </dl>
        </section>
      </div>

      <div v-else-if="tab === 'content'" class="inspector-body wiki-content">
        <p v-if="pageLoading" class="inspector-state" role="status">正在读取当前知识版本...</p>
        <p v-else-if="pageError" class="inspector-state error" role="alert">{{ pageError }}</p>
        <template v-else-if="pageDocument">
          <header>
            <strong>{{ page?.title || '知识正文' }}</strong>
            <span>版本 {{ pageDocument.revision.sequence }}</span>
          </header>
          <article v-html="render(pageDocument.content)"></article>
          <p v-if="pageDocument.truncated" class="content-truncated">正文已按浏览器展示上限截断。</p>
        </template>
      </div>

      <div v-else-if="tab === 'evidence'" class="inspector-body evidence-list">
        <p v-if="!evidence.length" class="inspector-state">当前邻域没有可展示证据。</p>
        <article v-for="item in evidence" :key="item.citation_id">
          <header><strong>{{ item.sourceLabel }}</strong><em>{{ item.relation }}</em></header>
          <code :title="item.sourcePath">{{ item.sourcePath }}</code>
          <dl>
            <div><dt>证据片段</dt><dd>{{ item.chunk_id.slice(0, 18) }}</dd></div>
            <div><dt>Wiki revision</dt><dd>{{ item.page_revision.slice(0, 18) }}</dd></div>
            <div><dt>Source revision</dt><dd>{{ item.source_revision.slice(0, 18) }}</dd></div>
          </dl>
          <button
            type="button"
            class="evidence-toggle"
            :aria-label="`查看 ${item.sourcePath} 的证据片段`"
            :aria-expanded="expandedEvidenceId === item.citation_id"
            @click="toggleEvidence(item.citation_id)"
          >
            <LoaderCircle v-if="evidenceLoading.has(item.citation_id)" :size="14" class="spinning" />
            <FileText v-else :size="14" />
            {{ evidenceLoading.has(item.citation_id) ? '读取中' : '查看证据片段' }}
            <ChevronDown :size="14" :class="{ expanded: expandedEvidenceId === item.citation_id }" />
          </button>
          <div v-if="expandedEvidenceId === item.citation_id" class="evidence-preview">
            <p v-if="evidenceErrors[item.citation_id]" role="alert">{{ evidenceErrors[item.citation_id] }}</p>
            <template v-else-if="evidenceDetails[item.citation_id]">
              <small>
                {{ evidenceDetails[item.citation_id].heading_path.join(' / ') || evidenceDetails[item.citation_id].title }}
                <template v-if="evidenceDetails[item.citation_id].page_number"> · 第 {{ evidenceDetails[item.citation_id].page_number }} 页</template>
              </small>
              <pre>{{ evidenceDetails[item.citation_id].excerpt }}</pre>
              <span v-if="evidenceDetails[item.citation_id].truncated">片段已按浏览器展示上限截断。</span>
            </template>
          </div>
        </article>
      </div>

      <div v-else class="inspector-body relation-list">
        <p v-if="!relationRows.length" class="inspector-state">当前节点没有一跳关系。</p>
        <button
          v-for="row in relationRows"
          :key="row.neighbor.node_id"
          type="button"
          @click="emit('select', row.neighbor.node_id)"
        >
          <span>
            <strong>{{ row.neighbor.label }}</strong>
            <small>
              {{ kindLabels[row.neighbor.kind] }}
              <template v-if="row.edge"> · {{ relationLabels[row.edge.kind] }} · {{ row.edge.evidence.length }} 条证据</template>
            </small>
          </span>
          <ArrowUpRight :size="15" />
        </button>
      </div>
    </template>

    <div v-else class="inspector-body goal-inspector">
      <section v-if="goal">
        <div class="section-title"><Target :size="16" /><h3>当前目标</h3></div>
        <p>{{ goal.description }}</p>
      </section>
      <section v-if="alignments.length" aria-labelledby="alignment-title">
        <div class="section-title"><Network :size="16" /><h3 id="alignment-title">能力覆盖</h3></div>
        <article v-for="item in alignments" :key="item.capability_id" class="alignment-row">
          <div><strong>{{ item.label }}</strong><span>{{ Math.round(item.coverage * 100) }}%</span></div>
          <div class="coverage-track"><i :style="{ width: `${item.coverage * 100}%` }"></i></div>
          <p v-if="item.missing_keywords.length">待补：{{ item.missing_keywords.join('、') }}</p>
        </article>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.knowledge-inspector { display:flex; flex-direction:column; min-width:0; min-height:0; border-left:1px solid var(--sage-border); background:var(--sage-surface); }.inspector-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; min-height:72px; padding:14px 16px; border-bottom:1px solid var(--sage-border); }.inspector-heading>div:first-child { min-width:0; }.inspector-heading span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); text-transform:uppercase; }.inspector-heading h2 { overflow:hidden; margin:3px 0 0; font-size:16px; line-height:1.35; text-overflow:ellipsis; white-space:nowrap; }.inspector-heading-actions { display:flex; flex:none; gap:2px; }.inspector-heading button { display:grid; place-items:center; width:32px; height:32px; flex:none; padding:0; border:0; border-radius:var(--sage-radius); color:var(--sage-text-muted); background:transparent; }.inspector-heading button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }
.inspector-tabs { display:grid; grid-template-columns:repeat(3,1fr); min-height:44px; border-bottom:1px solid var(--sage-border); }.inspector-tabs.has-content { grid-template-columns:repeat(4,1fr); }.inspector-tabs.page-only { grid-template-columns:repeat(2,1fr); }.inspector-tabs button { border:0; border-bottom:2px solid transparent; color:var(--sage-text-muted); background:transparent; font-size:var(--sage-font-sm); }.inspector-tabs button.active { border-color:var(--sage-source); color:var(--sage-text); font-weight:650; }
.inspector-body { min-height:0; overflow:auto; padding:2px 17px 24px; }.inspector-body section { padding:17px 0; border-bottom:1px solid var(--sage-border); }.section-title { display:flex; align-items:center; gap:7px; margin-bottom:10px; color:var(--sage-text-secondary); }.section-title h3 { margin:0; font-size:var(--sage-font-sm); }.inspector-body section>strong { overflow-wrap:anywhere; font-size:var(--sage-font-md); }.inspector-body p { margin:5px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-sm); line-height:1.55; }.inspector-body code { display:block; overflow:hidden; color:var(--sage-text-secondary); font-family:var(--sage-font-mono); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.inspector-body article[data-severity] { padding:9px 10px; border-left:3px solid var(--sage-source); background:var(--sage-surface-muted); }.inspector-body article[data-severity="high"] { border-color:var(--sage-coral); }.inspector-body article[data-severity="medium"] { border-color:var(--sage-review); }.inspector-body article+article { margin-top:7px; }.inspector-body article strong { font-size:var(--sage-font-sm); }.inspector-body article p { font-size:var(--sage-font-xs); }dl { margin:0; }dl div { display:flex; justify-content:space-between; gap:12px; min-height:30px; }dt { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }dd { overflow:hidden; margin:0; font-family:var(--sage-font-mono); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.inspector-state { padding:24px 0; color:var(--sage-text-muted); text-align:center; }.evidence-list article { padding:13px 0; border-bottom:1px solid var(--sage-border); }.evidence-list header { display:flex; align-items:center; justify-content:space-between; gap:8px; }.evidence-list strong { overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.evidence-list em { flex:none; padding:2px 5px; border-radius:var(--sage-radius); color:var(--sage-source); background:var(--sage-source-bg); font-size:10px; font-style:normal; }.evidence-list code { margin-top:5px; color:var(--sage-text-muted); }.evidence-list dl { margin-top:9px; }.evidence-list dl div { min-height:24px; }.relation-list button { display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%; min-height:58px; padding:7px 2px; border:0; border-bottom:1px solid var(--sage-border); text-align:left; color:var(--sage-text); background:transparent; }.relation-list button:hover { color:var(--sage-source); }.relation-list span,.relation-list strong,.relation-list small { display:block; min-width:0; }.relation-list strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:var(--sage-font-sm); }.relation-list small { margin-top:3px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
.evidence-toggle { display:flex; align-items:center; gap:6px; width:100%; min-height:32px; margin-top:8px; padding:0 7px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); color:var(--sage-text-secondary); background:var(--sage-surface-muted); font-size:var(--sage-font-xs); }.evidence-toggle:hover { color:var(--sage-source); border-color:var(--sage-source); }.evidence-toggle svg:last-child { margin-left:auto; transition:transform .16s ease; }.evidence-toggle svg.expanded { transform:rotate(180deg); }.evidence-preview { margin-top:7px; padding:9px 10px; border-left:2px solid var(--sage-source); background:var(--sage-surface-muted); }.evidence-preview small,.evidence-preview span { display:block; color:var(--sage-text-muted); font-size:10px; }.evidence-preview pre { max-height:220px; overflow:auto; margin:7px 0 0; color:var(--sage-text-secondary); font-family:var(--sage-font-mono); font-size:11px; line-height:1.55; white-space:pre-wrap; overflow-wrap:anywhere; }.evidence-preview p { color:var(--sage-danger); }.spinning { animation:spin 1s linear infinite; }
.wiki-content { padding-top:14px; }.wiki-content>header { padding-bottom:11px; border-bottom:1px solid var(--sage-border); }.wiki-content>header code { white-space:normal; overflow-wrap:anywhere; }.wiki-content>header span { display:block; margin-top:4px; color:var(--sage-text-muted); font-size:10px; }.wiki-content>article { color:var(--sage-text-secondary); font-size:var(--sage-font-sm); line-height:1.7; overflow-wrap:anywhere; }.wiki-content :deep(h1),.wiki-content :deep(h2),.wiki-content :deep(h3) { margin:18px 0 8px; color:var(--sage-text); line-height:1.35; }.wiki-content :deep(h1) { font-size:20px; }.wiki-content :deep(h2) { font-size:17px; }.wiki-content :deep(h3) { font-size:15px; }.wiki-content :deep(p),.wiki-content :deep(ul),.wiki-content :deep(ol) { margin:8px 0; }.wiki-content :deep(pre) { overflow:auto; padding:10px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); background:var(--sage-surface-muted); }.wiki-content :deep(code) { white-space:pre-wrap; }.wiki-content :deep(a) { color:var(--sage-source); }.content-truncated { color:var(--sage-review-strong) !important; }
.goal-inspector { padding-top:4px; }.alignment-row { padding:10px 0 !important; border:0 !important; background:transparent !important; }.alignment-row>div:first-child { display:flex; justify-content:space-between; gap:12px; }.alignment-row span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.coverage-track { height:5px; margin-top:7px; overflow:hidden; border-radius:3px; background:var(--sage-border); }.coverage-track i { display:block; height:100%; background:var(--sage-brand); }.alignment-row p { font-size:11px; }
.knowledge-inspector.compact { position:fixed; z-index:60; inset:52px 0 0 auto; width:min(380px,100vw); box-shadow:var(--sage-shadow-drawer); }
@media (max-width:600px) { .knowledge-inspector.compact { inset:0; width:100%; }.inspector-heading { min-height:60px; } }
@keyframes spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion:reduce) { .spinning { animation:none; }.evidence-toggle svg:last-child { transition:none; } }
</style>
