<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  AlertTriangle,
  BookOpenText,
  Check,
  ChevronRight,
  CircleDot,
  Database,
  FileClock,
  FilePlus2,
  Filter,
  FolderOpen,
  FolderUp,
  GitBranch,
  History,
  LayoutList,
  Network,
  PanelLeftOpen,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  Square,
  Target,
  Upload,
  X,
} from 'lucide-vue-next'
import {
  applyPendingKnowledgeMigration,
  cancelKnowledgeJob,
  createKnowledgeJob,
  fetchKnowledgeGraph,
  fetchKnowledgeGraphCommunities,
  fetchKnowledgeGraphInsights,
  fetchKnowledgeIndex,
  fetchKnowledgeJobs,
  fetchKnowledgeLearningGoal,
  fetchKnowledgeGraphNeighborhood,
  fetchKnowledgePages,
  fetchPendingKnowledgeMigration,
  fetchKnowledgeProposals,
  fetchKnowledgeSummary,
  ingestKnowledgeSource,
  previewKnowledgeSync,
  proposeKnowledgeRollback,
  rebuildKnowledgeGraph,
  rebuildKnowledgeIndex,
  retryKnowledgeJobItem,
  transitionKnowledgeProposal,
  undoKnowledgeAutoApply,
} from '../api/knowledge'
import { KnowledgeGraphCanvas, KnowledgeInspector } from '../components/knowledge'
import { ChatHarnessLayout, HarnessContextSummary } from '../components/harness'
import { knowledgeSurfaceAdapter } from '../harness/surfaces/knowledge'
import type {
  KnowledgeGoalAlignment,
  KnowledgeGraph,
  KnowledgeGraphCommunities,
  KnowledgeGraphInsight,
  KnowledgeGraphNeighborhood,
  KnowledgeGraphNode,
  KnowledgeGraphNodeKind,
  KnowledgeIndexSummary,
  KnowledgeJob,
  KnowledgeLearningGoal,
  KnowledgeMigrationPlan,
  KnowledgeMigrationResult,
  KnowledgePage,
  KnowledgeProposal,
  KnowledgeSyncPlan,
  KnowledgeWorkspaceSummary,
} from '../types/api'

type WorkspaceMode = 'graph' | 'wiki' | 'activity' | 'attention'

const summary = ref<KnowledgeWorkspaceSummary | null>(null)
const graph = ref<KnowledgeGraph | null>(null)
const communities = ref<KnowledgeGraphCommunities | null>(null)
const insights = ref<KnowledgeGraphInsight[]>([])
const alignments = ref<KnowledgeGoalAlignment[]>([])
const goal = ref<KnowledgeLearningGoal | null>(null)
const pages = ref<KnowledgePage[]>([])
const jobs = ref<KnowledgeJob[]>([])
const proposals = ref<KnowledgeProposal[]>([])
const autoApplied = ref<KnowledgeProposal[]>([])
const indexSummary = ref<KnowledgeIndexSummary | null>(null)
const migrationPlan = ref<KnowledgeMigrationPlan | null>(null)
const migrationResult = ref<KnowledgeMigrationResult | null>(null)
const syncPlan = ref<KnowledgeSyncPlan | null>(null)
const neighborhood = ref<KnowledgeGraphNeighborhood | null>(null)
const selectedNodeId = ref<string | null>(null)
const mode = ref<WorkspaceMode>('graph')
const colorMode = ref<'type' | 'community'>('community')
const visibleKinds = ref<KnowledgeGraphNodeKind[]>([
  'page', 'source', 'project', 'concept', 'decision', 'tool',
])
const query = ref('')
const relativePath = ref('')
const relativeDirectory = ref('.')
const selectedRoot = ref('')
const loading = ref(true)
const nodeLoading = ref(false)
const error = ref('')
const notice = ref('')
const busy = ref<Record<string, boolean>>({})
const importOpen = ref(false)
const libraryOpen = ref(false)
const viewportWidth = ref(window.innerWidth)
const jobsAvailable = ref(true)
let pollTimer: ReturnType<typeof setInterval> | null = null
let nodeRequest = 0

const tabs: Array<{ id: WorkspaceMode; label: string; icon: typeof Network }> = [
  { id: 'graph', label: '知识图谱', icon: Network },
  { id: 'wiki', label: 'Wiki', icon: BookOpenText },
  { id: 'activity', label: '同步活动', icon: History },
  { id: 'attention', label: '需要关注', icon: AlertTriangle },
]

const kindLabels: Record<KnowledgeGraphNodeKind, string> = {
  page: '页面', source: '来源', project: '项目', concept: '概念', decision: '决策', tool: '工具',
}
const changeKindLabels = { added: '新增', modified: '修改', deleted: '删除' } as const

const compactLibrary = computed(() => viewportWidth.value < 1360)
const selectedNode = computed<KnowledgeGraphNode | null>(() =>
  graph.value?.nodes.find((item) => item.node_id === selectedNodeId.value) ?? null,
)
const selectedPage = computed<KnowledgePage | null>(() => {
  const pageId = selectedNode.value?.page_id
  return pageId ? pages.value.find((item) => item.page_id === pageId) ?? null : null
})
const selectedCommunityId = computed(() => communities.value?.node_metrics.find(
  (item) => item.node_id === selectedNodeId.value,
)?.community_id ?? null)
const filteredPages = computed(() => {
  const value = query.value.trim().toLocaleLowerCase()
  if (!value) return pages.value
  return pages.value.filter((item) =>
    item.title.toLocaleLowerCase().includes(value) || item.path.toLocaleLowerCase().includes(value),
  )
})
const activeJobs = computed(() => jobs.value.filter((job) => !isTerminal(job.status)))
const knowledgeContext = computed(() => knowledgeSurfaceAdapter.buildContext({
  workspaceId: graph.value?.snapshot.workspace_id ?? jobs.value[0]?.workspace_id ?? 'knowledge-local',
  graphRevision: graph.value?.snapshot.graph_revision,
  selectedNode: selectedNode.value,
  selectedPage: selectedPage.value,
  activeJobs: activeJobs.value,
}))
const operationLabels = computed(() => Object.fromEntries(activeJobs.value.map((job) => [
  job.job_id,
  `${job.source_label} / ${job.relative_directory}`,
])))
const attentionCount = computed(() => proposals.value.length + insights.value.filter(
  (item) => item.severity === 'high',
).length)
const selectedSource = computed(() => summary.value?.source_roots.find(
  (item) => item.root_id === selectedRoot.value,
))
const migrationActionCount = computed(() => {
  const plan = migrationPlan.value
  return plan ? plan.auto_apply_count + plan.retire_count + plan.block_count : 0
})

function setBusy(key: string, active: boolean) {
  const next = { ...busy.value }
  if (active) next[key] = true
  else delete next[key]
  busy.value = next
}

function explain(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason)
}

async function refreshWorkspace() {
  loading.value = true
  error.value = ''
  try {
    const [nextSummary, nextPages, nextProposals, nextIndex, nextGraph] = await Promise.all([
      fetchKnowledgeSummary(),
      fetchKnowledgePages(),
      fetchKnowledgeProposals(null),
      fetchKnowledgeIndex(),
      fetchKnowledgeGraph(),
    ])
    const [nextJobs, nextCommunities, nextInsights, nextGoal] = await Promise.all([
      fetchKnowledgeJobs().catch(() => {
        jobsAvailable.value = false
        return []
      }),
      fetchKnowledgeGraphCommunities(),
      fetchKnowledgeGraphInsights(),
      fetchKnowledgeLearningGoal(),
    ])
    summary.value = nextSummary
    pages.value = nextPages
    graph.value = nextGraph
    communities.value = nextCommunities
    insights.value = nextInsights.insights
    alignments.value = nextInsights.alignments
    goal.value = nextGoal
    jobs.value = nextJobs
    indexSummary.value = nextIndex
    proposals.value = nextProposals.filter((item) => item.status === 'pending')
    autoApplied.value = nextProposals.filter(
      (item) => item.policy_decision?.action === 'auto_apply'
        && item.policy_decision.undo_available,
    ).slice(0, 12)
    if (!selectedRoot.value) selectedRoot.value = nextSummary.source_roots[0]?.root_id ?? ''
    if (selectedNodeId.value && !nextGraph.nodes.some(
      (item) => item.node_id === selectedNodeId.value,
    )) selectNode(null)
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    loading.value = false
  }
}

async function refreshJobs() {
  if (!jobsAvailable.value || activeJobs.value.length === 0) return
  try {
    const activeIds = new Set(activeJobs.value.map((job) => job.job_id))
    const nextJobs = await fetchKnowledgeJobs()
    const finished = nextJobs.some(
      (job) => activeIds.has(job.job_id) && isTerminal(job.status),
    )
    jobs.value = nextJobs
    if (finished) await refreshWorkspace()
  } catch (reason) {
    error.value = explain(reason)
  }
}

async function selectNode(nodeId: string | null) {
  selectedNodeId.value = nodeId
  neighborhood.value = null
  if (!nodeId) return
  const requestId = ++nodeRequest
  nodeLoading.value = true
  try {
    const next = await fetchKnowledgeGraphNeighborhood(nodeId)
    if (requestId === nodeRequest && selectedNodeId.value === nodeId) neighborhood.value = next
  } catch (reason) {
    if (requestId === nodeRequest) error.value = explain(reason)
  } finally {
    if (requestId === nodeRequest) nodeLoading.value = false
  }
}

function closeInspector() {
  void selectNode(null)
}

function selectPage(page: KnowledgePage) {
  const node = graph.value?.nodes.find((item) => item.page_id === page.page_id)
  mode.value = 'graph'
  libraryOpen.value = false
  if (node) void selectNode(node.node_id)
  else notice.value = '该页面尚未进入当前图谱 revision，请先重建图谱。'
}

function toggleKind(kind: KnowledgeGraphNodeKind) {
  visibleKinds.value = visibleKinds.value.includes(kind)
    ? visibleKinds.value.filter((item) => item !== kind)
    : [...visibleKinds.value, kind]
}

async function rebuildGraphProjection() {
  setBusy('graph', true)
  error.value = ''
  try {
    await rebuildKnowledgeGraph()
    const [nextGraph, nextCommunities, nextInsights] = await Promise.all([
      fetchKnowledgeGraph(),
      fetchKnowledgeGraphCommunities(),
      fetchKnowledgeGraphInsights(),
    ])
    graph.value = nextGraph
    communities.value = nextCommunities
    insights.value = nextInsights.insights
    alignments.value = nextInsights.alignments
    notice.value = `图谱已更新：${nextGraph.snapshot.node_count} 个节点，${nextGraph.snapshot.edge_count} 条连接。`
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy('graph', false)
  }
}

async function rebuildIndex() {
  setBusy('index', true)
  error.value = ''
  try {
    indexSummary.value = await rebuildKnowledgeIndex()
    notice.value = '检索索引已按当前 Wiki revision 重建。'
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy('index', false)
  }
}

async function ingest() {
  const path = relativePath.value.trim()
  if (!selectedRoot.value || !path) return
  setBusy('ingest', true)
  error.value = ''
  try {
    await ingestKnowledgeSource(selectedRoot.value, path)
    relativePath.value = ''
    importOpen.value = false
    notice.value = '来源已进入解析流程；可信结果会自动沉淀，异常项才需要确认。'
    await refreshWorkspace()
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy('ingest', false)
  }
}

async function createBatch() {
  if (!selectedRoot.value || !jobsAvailable.value) return
  const directory = relativeDirectory.value.trim() || '.'
  if (
    !syncPlan.value
    || syncPlan.value.source_root_id !== selectedRoot.value
    || syncPlan.value.relative_directory !== directory
  ) {
    await previewBatch()
    return
  }
  if (syncPlan.value.total_count === 0) return
  setBusy('batch', true)
  error.value = ''
  try {
    const job = await createKnowledgeJob(selectedRoot.value, directory, syncPlan.value.plan_id)
    jobs.value = [job, ...jobs.value]
    mode.value = 'activity'
    importOpen.value = false
    notice.value = `已开始同步 ${syncPlan.value.total_count} 项变更；删除项只生成可审核 tombstone。`
    syncPlan.value = null
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy('batch', false)
  }
}

async function previewBatch() {
  if (!selectedRoot.value || !jobsAvailable.value) return
  setBusy('preview', true)
  error.value = ''
  try {
    syncPlan.value = await previewKnowledgeSync(
      selectedRoot.value,
      relativeDirectory.value.trim() || '.',
    )
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy('preview', false)
  }
}

async function decide(proposal: KnowledgeProposal, action: 'approve' | 'reject') {
  setBusy(proposal.proposal_id, true)
  error.value = ''
  try {
    await transitionKnowledgeProposal(proposal.proposal_id, action, proposal.revision)
    notice.value = action === 'approve' ? '异常知识已批准并形成新 revision。' : '异常知识已拒绝。'
    await refreshWorkspace()
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy(proposal.proposal_id, false)
  }
}

async function undoAutoApply(proposal: KnowledgeProposal) {
  const revision = proposal.policy_decision?.applied_page_revision
  if (!revision) return
  const key = `undo:${proposal.proposal_id}`
  setBusy(key, true)
  error.value = ''
  try {
    await undoKnowledgeAutoApply(proposal.proposal_id, revision)
    notice.value = '已为自动沉淀内容创建撤销 revision。'
    await refreshWorkspace()
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy(key, false)
  }
}

async function rollback(page: KnowledgePage, revisionId: string) {
  const key = `rollback:${page.page_id}`
  setBusy(key, true)
  error.value = ''
  try {
    await proposeKnowledgeRollback(page.page_id, revisionId, page.current_revision)
    notice.value = '回滚已生成可审核 diff，不会覆盖历史 revision。'
    mode.value = 'attention'
    await refreshWorkspace()
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy(key, false)
  }
}

async function cancelJob(job: KnowledgeJob) {
  const key = `cancel:${job.job_id}`
  setBusy(key, true)
  try {
    await cancelKnowledgeJob(job.job_id)
    jobs.value = await fetchKnowledgeJobs()
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy(key, false)
  }
}

async function retryItem(job: KnowledgeJob, itemId: string) {
  const key = `retry:${itemId}`
  setBusy(key, true)
  try {
    await retryKnowledgeJobItem(job.job_id, itemId)
    jobs.value = await fetchKnowledgeJobs()
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy(key, false)
  }
}

async function applyMigration() {
  const plan = migrationPlan.value
  if (!plan || migrationActionCount.value === 0) return
  setBusy('migration', true)
  try {
    migrationResult.value = await applyPendingKnowledgeMigration(plan.plan_id)
    await refreshWorkspace()
  } catch (reason) {
    error.value = explain(reason)
  } finally {
    setBusy('migration', false)
  }
}

function isTerminal(status: string) {
  return ['completed', 'completed_with_errors', 'cancelled'].includes(status)
}

function jobStatus(status: string) {
  return ({
    queued: '排队中', running: '解析中', cancelling: '取消中', completed: '已完成',
    completed_with_errors: '部分失败', cancelled: '已取消',
  } as Record<string, string>)[status] ?? status
}

function changeKindLabel(kind: keyof typeof changeKindLabels) {
  return changeKindLabels[kind]
}

function jobPercent(job: KnowledgeJob) {
  return job.total_items ? Math.round((job.processed_items / job.total_items) * 100) : 0
}

function formatTime(value: string | null | undefined) {
  if (!value) return '尚未同步'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

function updateViewport() {
  viewportWidth.value = window.innerWidth
  if (!compactLibrary.value) libraryOpen.value = false
}

watch([selectedRoot, relativeDirectory], () => {
  syncPlan.value = null
})

onMounted(() => {
  window.addEventListener('resize', updateViewport)
  void fetchPendingKnowledgeMigration().then((value) => {
    migrationPlan.value = value
  }).catch(() => undefined)
  void refreshWorkspace()
  pollTimer = setInterval(() => void refreshJobs(), 3000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateViewport)
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="knowledge-workspace">
    <header class="workspace-header">
      <div class="workspace-title">
        <button
          v-if="compactLibrary"
          type="button"
          class="icon-button"
          title="打开知识目录"
          aria-label="打开知识目录"
          @click="libraryOpen = true"
        ><PanelLeftOpen :size="18" /></button>
        <span class="title-mark"><Network :size="19" /></span>
        <div>
          <strong>{{ summary?.workspace_name || '本地知识空间' }}</strong>
          <small>
            <i :class="{ ready: Boolean(summary) }"></i>
            {{ graph?.snapshot.stale ? '图谱需要更新' : summary ? 'Git 与索引已连接' : '正在连接' }}
          </small>
        </div>
      </div>
      <div class="header-actions">
        <code v-if="graph">{{ graph.snapshot.graph_revision.slice(0, 18) }}</code>
        <button type="button" class="secondary-button" :disabled="busy.graph" @click="rebuildGraphProjection">
          <RefreshCw :size="15" :class="{ spinning: busy.graph }" />
          <span>{{ busy.graph ? '更新中' : '同步图谱' }}</span>
        </button>
        <button type="button" class="primary-button" @click="importOpen = true">
          <Upload :size="15" /><span>导入来源</span>
        </button>
      </div>
    </header>

    <p v-if="error" class="workspace-alert error" role="alert">
      <AlertTriangle :size="16" />{{ error }}
      <button type="button" @click="refreshWorkspace">重试</button>
    </p>
    <p v-else-if="notice" class="workspace-alert notice" role="status">
      <Check :size="16" />{{ notice }}
      <button type="button" aria-label="关闭提示" title="关闭" @click="notice = ''"><X :size="15" /></button>
    </p>

    <div class="workspace-body" :class="{ 'is-loading': loading }">
      <div
        v-if="compactLibrary && libraryOpen"
        class="panel-backdrop"
        aria-hidden="true"
        @click="libraryOpen = false"
      ></div>
      <aside class="knowledge-library" :class="{ compact: compactLibrary, open: libraryOpen }">
        <header>
          <div><strong>知识目录</strong><small>{{ pages.length }} 个 Wiki 页面</small></div>
          <button
            v-if="compactLibrary"
            type="button"
            class="icon-button"
            title="关闭目录"
            aria-label="关闭知识目录"
            @click="libraryOpen = false"
          ><X :size="17" /></button>
        </header>

        <section class="library-metrics" aria-label="知识库指标">
          <button type="button" @click="mode = 'wiki'; libraryOpen = false">
            <strong>{{ summary?.wiki_page_count ?? 0 }}</strong><span>已沉淀</span>
          </button>
          <button type="button" @click="mode = 'activity'; libraryOpen = false">
            <strong>{{ activeJobs.length }}</strong><span>同步中</span>
          </button>
          <button type="button" @click="mode = 'attention'; libraryOpen = false">
            <strong>{{ attentionCount }}</strong><span>需关注</span>
          </button>
        </section>

        <label class="library-search">
          <Search :size="15" aria-hidden="true" />
          <input v-model="query" type="search" aria-label="搜索知识库" placeholder="搜索页面或节点" />
        </label>

        <section v-if="goal" class="goal-summary">
          <span><Target :size="15" />当前学习目标</span>
          <strong>{{ goal.title }}</strong>
          <small>{{ goal.capabilities.length }} 项能力模型 · {{ goal.goal_revision.slice(0, 13) }}</small>
        </section>

        <nav class="library-nav" aria-label="知识空间视图">
          <button
            v-for="tabItem in tabs"
            :key="tabItem.id"
            type="button"
            :class="{ active: mode === tabItem.id }"
            @click="mode = tabItem.id; libraryOpen = false"
          >
            <component :is="tabItem.icon" :size="16" />
            <span>{{ tabItem.label }}</span>
            <em v-if="tabItem.id === 'attention' && attentionCount">{{ attentionCount }}</em>
            <ChevronRight v-else :size="14" />
          </button>
        </nav>

        <div class="page-tree">
          <div class="tree-heading"><span>Wiki 页面</span><small>{{ filteredPages.length }}</small></div>
          <button v-for="page in filteredPages.slice(0, 80)" :key="page.page_id" type="button" @click="selectPage(page)">
            <BookOpenText :size="14" />
            <span><strong>{{ page.title }}</strong><small>{{ page.revisions.length }} 个 revision</small></span>
          </button>
          <p v-if="!filteredPages.length">没有匹配的页面。</p>
        </div>

        <button type="button" class="library-import" @click="importOpen = true; libraryOpen = false">
          <FilePlus2 :size="15" />添加知识来源
        </button>
      </aside>

      <ChatHarnessLayout class="knowledge-harness" surface-label="Knowledge">
        <template #canvas>
          <main class="knowledge-stage">
        <header class="stage-toolbar">
          <div class="stage-tabs" role="tablist" aria-label="知识空间内容">
            <button
              v-for="tabItem in tabs"
              :key="tabItem.id"
              type="button"
              role="tab"
              :aria-selected="mode === tabItem.id"
              :class="{ active: mode === tabItem.id }"
              @click="mode = tabItem.id"
            >{{ tabItem.label }}</button>
          </div>
          <div v-if="mode === 'graph'" class="graph-filters">
            <label title="节点着色方式">
              <CircleDot :size="15" />
              <select v-model="colorMode" aria-label="图谱着色方式">
                <option value="community">按社区</option>
                <option value="type">按类型</option>
              </select>
            </label>
            <details>
              <summary><Filter :size="15" />筛选</summary>
              <div class="kind-menu">
                <label v-for="kind in (Object.keys(kindLabels) as KnowledgeGraphNodeKind[])" :key="kind">
                  <input
                    type="checkbox"
                    :checked="visibleKinds.includes(kind)"
                    @change="toggleKind(kind)"
                  />{{ kindLabels[kind] }}
                </label>
              </div>
            </details>
          </div>
        </header>

        <div v-if="loading" class="stage-state" role="status">
          <RefreshCw :size="20" class="spinning" /><strong>正在装配本地图谱</strong>
          <span>读取 Git Wiki revision、引用证据与社区分析...</span>
        </div>

        <template v-else-if="summary">
          <section v-if="mode === 'graph'" class="graph-stage" aria-labelledby="graph-title">
            <div v-if="graph && graph.nodes.length" class="graph-heading">
              <div>
                <h1 id="graph-title">知识图谱</h1>
                <span>{{ graph.snapshot.node_count }} 个节点 · {{ graph.snapshot.edge_count }} 条证据连接 · {{ communities?.analysis.community_count ?? 0 }} 个社区</span>
              </div>
              <div class="graph-legend" aria-label="节点图例">
                <span><i class="page"></i>页面</span><span><i class="concept"></i>概念</span>
                <span><i class="source"></i>来源</span><span><i class="tool"></i>工具</span>
              </div>
            </div>
            <KnowledgeGraphCanvas
              v-if="graph && graph.nodes.length"
              :graph="graph"
              :communities="communities"
              :selected-node-id="selectedNodeId"
              :color-mode="colorMode"
              :visible-kinds="visibleKinds"
              :query="query"
              @select="selectNode"
            />
            <div v-else class="stage-state empty">
              <Network :size="26" /><strong>当前 revision 还没有图谱节点</strong>
              <span>先从已授权来源导入 Markdown、HTML 或文本 PDF，再生成本地图谱。</span>
              <button type="button" class="primary-button" @click="importOpen = true">导入来源</button>
            </div>
          </section>

          <section v-else-if="mode === 'wiki'" class="list-stage" aria-labelledby="wiki-title">
            <header><div><h1 id="wiki-title">Git Wiki</h1><p>每次沉淀和回滚都会形成新的 revision，不覆盖来源和历史。</p></div><strong>{{ pages.length }}</strong></header>
            <div class="wiki-list">
              <article v-for="page in filteredPages" :key="page.page_id">
                <button type="button" class="wiki-main" @click="selectPage(page)">
                  <BookOpenText :size="18" />
                  <span><strong>{{ page.title }}</strong><code>{{ page.path }}</code></span>
                </button>
                <div><span>{{ page.revisions.length }} 个版本</span><span>{{ formatTime(page.updated_at) }}</span></div>
                <button
                  v-if="page.revisions.length > 1"
                  type="button"
                  class="icon-button"
                  title="回滚到上一个 revision"
                  aria-label="回滚到上一个 revision"
                  :disabled="busy[`rollback:${page.page_id}`]"
                  @click="rollback(page, page.revisions[1].revision_id)"
                ><RotateCcw :size="16" /></button>
              </article>
              <p v-if="!filteredPages.length" class="empty-row">没有匹配的 Wiki 页面。</p>
            </div>
          </section>

          <section v-else-if="mode === 'activity'" class="list-stage" aria-labelledby="activity-title">
            <header>
              <div><h1 id="activity-title">同步活动</h1><p>目录解析在服务端持久运行，离开页面不会中止任务。</p></div>
              <button type="button" class="secondary-button" :disabled="busy.index" @click="rebuildIndex">
                <Database :size="15" />{{ busy.index ? '重建中' : '重建索引' }}
              </button>
            </header>
            <div class="index-line" v-if="indexSummary">
              <Database :size="17" />
              <span><strong>{{ indexSummary.status === 'ready' ? 'Hybrid 索引就绪' : '索引已降级' }}</strong><small>{{ indexSummary.indexed_revision_count }}/{{ indexSummary.revision_count }} revisions · {{ indexSummary.active_chunk_count }} chunks</small></span>
              <code>{{ indexSummary.embedding_model }}@{{ indexSummary.embedding_revision }}</code>
            </div>
            <div class="projection-status" aria-label="知识投影状态">
              <span><BookOpenText :size="14" /><strong>Wiki</strong>{{ pages.length }} 页</span>
              <span><Database :size="14" /><strong>Index</strong>{{ indexSummary?.status === 'ready' ? '已同步' : '需重建' }}</span>
              <span :data-stale="graph?.snapshot.stale"><Network :size="14" /><strong>Graph</strong>{{ graph?.snapshot.stale ? '需重建' : '已同步' }}</span>
            </div>
            <p v-if="!jobsAvailable" class="inline-warning">持久任务尚未启用；单文件导入仍可使用。</p>
            <div class="job-list">
              <article v-for="job in jobs" :key="job.job_id">
                <header><span><strong>{{ job.source_label }} / {{ job.relative_directory }}</strong><small>{{ jobStatus(job.status) }} · {{ job.processed_items }}/{{ job.total_items }}</small></span><code>{{ job.job_id.slice(0, 16) }}</code></header>
                <div class="job-progress" role="progressbar" :aria-valuenow="jobPercent(job)" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${jobPercent(job)}%` }"></i></div>
                <footer>
                  <span>成功 {{ job.succeeded_items }}</span><span>跳过 {{ job.skipped_items }}</span><span>失败 {{ job.failed_items }}</span>
                  <button v-if="!isTerminal(job.status)" type="button" :disabled="busy[`cancel:${job.job_id}`]" @click="cancelJob(job)"><Square :size="13" />停止</button>
                </footer>
                <div v-if="job.items.some((item) => item.status === 'dead_letter')" class="failed-items">
                  <button v-for="item in job.items.filter((entry) => entry.status === 'dead_letter')" :key="item.item_id" type="button" :disabled="busy[`retry:${item.item_id}`]" @click="retryItem(job, item.item_id)">
                    <RefreshCw :size="13" />
                    <span><strong>重试 {{ changeKindLabel(item.change_kind) }}：{{ item.relative_path }}</strong><small>{{ item.error || '处理失败，可重新执行该项。' }}</small></span>
                  </button>
                </div>
              </article>
              <div v-if="!jobs.length" class="stage-state empty"><FileClock :size="24" /><strong>还没有同步任务</strong><span>导入一个目录后，解析进度会显示在这里。</span></div>
            </div>
          </section>

          <section v-else class="list-stage attention-stage" aria-labelledby="attention-title">
            <header><div><h1 id="attention-title">需要关注</h1><p>可信本地内容自动沉淀；这里只有异常解析和可解释的知识缺口。</p></div><strong>{{ attentionCount }}</strong></header>

            <div v-if="migrationPlan && migrationActionCount" class="migration-line">
              <Sparkles :size="18" />
              <span><strong>可自动整理 {{ migrationActionCount }} 条历史记录</strong><small>自动沉淀 {{ migrationPlan.auto_apply_count }} · 归档 {{ migrationPlan.retire_count }} · 拦截 {{ migrationPlan.block_count }}</small></span>
              <button type="button" class="secondary-button" :disabled="busy.migration" @click="applyMigration">一键整理</button>
            </div>
            <p v-if="migrationResult" class="inline-success">已沉淀 {{ migrationResult.auto_applied_count }} 条，归档 {{ migrationResult.retired_count }} 条。</p>

            <div class="attention-grid">
              <section>
                <h2>异常解析 <span>{{ proposals.length }}</span></h2>
                <article v-for="proposal in proposals" :key="proposal.proposal_id" class="proposal-row">
                  <header><span><strong>{{ proposal.title }}</strong><code>{{ proposal.source_relative_path }}</code></span><em>{{ proposal.change_kind }}</em></header>
                  <pre>{{ proposal.diff }}</pre>
                  <footer>
                    <button type="button" class="reject" :disabled="busy[proposal.proposal_id]" @click="decide(proposal, 'reject')"><X :size="14" />忽略</button>
                    <button type="button" class="approve" :disabled="busy[proposal.proposal_id]" @click="decide(proposal, 'approve')"><Check :size="14" />批准 revision</button>
                  </footer>
                </article>
                <p v-if="!proposals.length" class="empty-row">没有需要人工确认的异常。</p>
              </section>

              <section>
                <h2>目标缺口 <span>{{ insights.length }}</span></h2>
                <article v-for="insight in insights.slice(0, 40)" :key="insight.insight_id" class="insight-row" :data-severity="insight.severity">
                  <CircleDot :size="14" /><span><strong>{{ insight.title }}</strong><p>{{ insight.description }}</p></span>
                </article>
                <p v-if="!insights.length" class="empty-row">当前没有图谱洞察。</p>
              </section>
            </div>

            <details v-if="autoApplied.length" class="auto-applied">
              <summary>最近自动沉淀 {{ autoApplied.length }} 条</summary>
              <article v-for="proposal in autoApplied" :key="proposal.proposal_id">
                <span><strong>{{ proposal.title }}</strong><code>{{ proposal.policy_decision?.applied_page_revision }}</code></span>
                <button type="button" :disabled="busy[`undo:${proposal.proposal_id}`]" @click="undoAutoApply(proposal)"><RotateCcw :size="14" />撤销</button>
              </article>
            </details>
          </section>
        </template>
          </main>
        </template>

        <template #chat>
          <HarnessContextSummary :context="knowledgeContext" :operation-labels="operationLabels" />
        </template>

        <template #details>
          <KnowledgeInspector
            :node="selectedNode"
            :page="selectedPage"
            :neighborhood="neighborhood"
            :insights="insights"
            :goal="goal"
            :alignments="alignments"
            :communities="communities?.communities ?? []"
            :community-id="selectedCommunityId"
            :loading="nodeLoading"
            :compact="false"
            @close="closeInspector"
            @select="selectNode"
          />
        </template>
      </ChatHarnessLayout>
    </div>

    <footer class="workspace-footer">
      <span><i :class="{ active: activeJobs.length > 0 }"></i>{{ activeJobs.length ? `${activeJobs.length} 个任务处理中` : '知识源空闲' }}</span>
      <span><GitBranch :size="14" />Wiki {{ summary?.wiki_page_count ?? 0 }}</span>
      <span><Network :size="14" />图谱 {{ graph?.snapshot.node_count ?? 0 }} / {{ graph?.snapshot.edge_count ?? 0 }}</span>
      <span><LayoutList :size="14" />社区 {{ communities?.analysis.community_count ?? 0 }}</span>
      <span class="footer-spacer"></span>
      <span>{{ formatTime(summary?.last_synced_at) }}</span>
    </footer>
  </div>

  <div v-if="importOpen" class="modal-backdrop" @click.self="importOpen = false">
    <section class="import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title" @keydown.esc="importOpen = false">
      <header><div><span><FolderUp :size="18" /></span><div><h2 id="import-title">导入知识来源</h2><p>从已授权的本地来源扫描，解析结果进入版本化 Git Wiki。</p></div></div><button type="button" class="icon-button" aria-label="关闭导入" title="关闭" @click="importOpen = false"><X :size="18" /></button></header>
      <label class="field-label">来源根目录
        <select v-model="selectedRoot" aria-label="知识来源根目录">
          <option v-for="root in summary?.source_roots ?? []" :key="root.root_id" :value="root.root_id">{{ root.label }} · {{ root.kind }}</option>
        </select>
      </label>
      <div class="import-options">
        <form @submit.prevent="ingest">
          <FilePlus2 :size="20" />
          <div><strong>单文件</strong><p>Markdown、HTML、文本 PDF；来源文件保持不变。</p></div>
          <input v-model="relativePath" type="text" aria-label="来源相对路径" placeholder="例如 docs/architecture.md" />
          <button type="submit" class="primary-button" :disabled="busy.ingest || !selectedRoot || !relativePath.trim()">{{ busy.ingest ? '解析中' : '解析文件' }}</button>
        </form>
        <form @submit.prevent="previewBatch">
          <FolderOpen :size="20" />
          <div><strong>整个目录</strong><p>先比较上次水位，只同步新增、修改和删除记录。</p></div>
          <input v-model="relativeDirectory" type="text" aria-label="来源相对目录" placeholder=". 或 notes/project" />
          <button type="submit" class="secondary-button" :disabled="busy.preview || busy.batch || !selectedRoot || !jobsAvailable">{{ busy.preview ? '检查中' : '检查更新' }}</button>
          <div v-if="syncPlan" class="sync-preview" aria-live="polite">
            <header>
              <strong v-if="syncPlan.total_count">发现 {{ syncPlan.total_count }} 项变更</strong>
              <strong v-else><Check :size="15" />当前目录已是最新</strong>
              <code>watermark {{ syncPlan.base_watermark }} → {{ syncPlan.target_watermark }}</code>
            </header>
            <div v-if="syncPlan.total_count" class="sync-counts">
              <span>新增 {{ syncPlan.added_count }}</span>
              <span>修改 {{ syncPlan.modified_count }}</span>
              <span>删除 {{ syncPlan.deleted_count }}</span>
            </div>
            <ul v-if="syncPlan.changes.length">
              <li v-for="change in syncPlan.changes.slice(0, 12)" :key="`${change.change_kind}:${change.relative_path}`">
                <em :data-kind="change.change_kind">{{ changeKindLabel(change.change_kind) }}</em>
                <code>{{ change.relative_path }}</code>
              </li>
            </ul>
            <small v-if="syncPlan.has_more || syncPlan.changes.length > 12">其余变更将在后台任务中继续处理。</small>
            <button
              v-if="syncPlan.total_count"
              type="button"
              class="primary-button"
              :disabled="busy.batch"
              @click="createBatch"
            >{{ busy.batch ? '正在启动' : `同步 ${syncPlan.total_count} 项更新` }}</button>
          </div>
        </form>
      </div>
      <footer><span><Database :size="14" />{{ selectedSource?.label || '未选择来源' }}</span><p>浏览器直接上传、多模态 Vision 与飞书来源将在后续连接器中开放。</p></footer>
    </section>
  </div>
</template>

<style scoped>
.knowledge-workspace { position:relative; display:grid; grid-template-rows:58px minmax(0,1fr) 40px; height:100dvh; min-width:0; overflow:hidden; background:var(--sage-bg); }
.workspace-header { display:flex; align-items:center; justify-content:space-between; gap:18px; padding:0 16px; border-bottom:1px solid var(--sage-border); background:var(--sage-surface); }.workspace-title,.workspace-title>div,.header-actions,.workspace-title small { display:flex; align-items:center; }.workspace-title { gap:10px; min-width:0; }.workspace-title>div { flex-direction:column; align-items:flex-start; min-width:0; line-height:1.3; }.workspace-title strong { max-width:320px; overflow:hidden; font-size:var(--sage-font-md); text-overflow:ellipsis; white-space:nowrap; }.workspace-title small { gap:5px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.workspace-title small i { width:7px; height:7px; border-radius:50%; background:var(--sage-border-strong); }.workspace-title small i.ready { background:var(--sage-success); }.title-mark { display:grid; place-items:center; width:34px; height:34px; flex:none; border-radius:var(--sage-radius); color:var(--sage-brand-strong); background:var(--sage-brand-bg); }.header-actions { gap:8px; }.header-actions code { max-width:180px; overflow:hidden; color:var(--sage-text-muted); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.icon-button,.primary-button,.secondary-button { display:inline-flex; align-items:center; justify-content:center; gap:6px; min-height:34px; border-radius:var(--sage-radius); padding:0 11px; font:inherit; font-size:var(--sage-font-sm); }.icon-button { width:34px; padding:0; border:0; color:var(--sage-text-secondary); background:transparent; }.icon-button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.primary-button { border:1px solid var(--sage-brand-strong); color:#fff; background:var(--sage-brand-strong); font-weight:650; }.secondary-button { border:1px solid var(--sage-border-strong); color:var(--sage-text-secondary); background:var(--sage-surface); }.primary-button:hover,.secondary-button:hover { filter:brightness(.97); }.primary-button:disabled,.secondary-button:disabled,.icon-button:disabled { cursor:not-allowed; opacity:.55; }
.workspace-alert { position:absolute; z-index:45; top:66px; left:50%; display:flex; align-items:center; gap:8px; max-width:min(620px,calc(100% - 32px)); margin:0; padding:9px 11px; border:1px solid var(--sage-border); border-left:3px solid var(--sage-brand); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); box-shadow:var(--sage-shadow-sm); font-size:var(--sage-font-sm); transform:translateX(-50%); }.workspace-alert.error { border-left-color:var(--sage-danger); }.workspace-alert button { margin-left:auto; border:0; color:var(--sage-source); background:transparent; }
.workspace-body { position:relative; display:grid; grid-template-columns:238px minmax(0,1fr); min-width:0; min-height:0; overflow:hidden; }.workspace-body.is-loading { grid-template-columns:238px minmax(0,1fr); }
.knowledge-harness { min-width:0; min-height:0; height:100%; }
.knowledge-library { z-index:2; display:flex; flex-direction:column; min-width:0; min-height:0; overflow:hidden; border-right:1px solid var(--sage-border); background:var(--sage-surface); }.knowledge-library>header { display:flex; align-items:center; justify-content:space-between; min-height:58px; padding:0 14px; border-bottom:1px solid var(--sage-border); }.knowledge-library>header div { display:flex; flex-direction:column; }.knowledge-library>header strong { font-size:var(--sage-font-md); }.knowledge-library>header small { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.library-metrics { display:grid; grid-template-columns:repeat(3,1fr); gap:1px; margin:12px; border:1px solid var(--sage-border); background:var(--sage-border); }.library-metrics button { min-width:0; min-height:62px; padding:8px 3px; border:0; color:var(--sage-text); background:var(--sage-surface); }.library-metrics button:hover { background:var(--sage-surface-muted); }.library-metrics strong,.library-metrics span { display:block; }.library-metrics strong { font-size:17px; }.library-metrics span { color:var(--sage-text-muted); font-size:11px; }.library-search { display:flex; align-items:center; gap:7px; margin:0 12px 12px; min-height:34px; padding:0 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:var(--sage-surface-raised); }.library-search:focus-within { border-color:var(--sage-source); }.library-search input { min-width:0; width:100%; border:0; outline:0; color:var(--sage-text); background:transparent; font-size:var(--sage-font-sm); }.goal-summary { margin:0 12px 12px; padding:11px; border-left:3px solid var(--sage-brand); background:var(--sage-brand-bg); }.goal-summary span { display:flex; align-items:center; gap:5px; color:var(--sage-brand-strong); font-size:11px; font-weight:700; }.goal-summary strong,.goal-summary small { display:block; }.goal-summary strong { margin-top:5px; font-size:var(--sage-font-sm); line-height:1.45; }.goal-summary small { margin-top:4px; color:var(--sage-text-muted); font-size:10px; }.library-nav { display:grid; gap:2px; padding:0 8px 10px; border-bottom:1px solid var(--sage-border); }.library-nav button { display:grid; grid-template-columns:20px minmax(0,1fr) auto; align-items:center; min-height:36px; padding:0 8px; border:0; border-radius:var(--sage-radius); color:var(--sage-text-secondary); text-align:left; background:transparent; font-size:var(--sage-font-sm); }.library-nav button:hover,.library-nav button.active { color:var(--sage-text); background:var(--sage-surface-muted); }.library-nav button.active { color:var(--sage-brand-strong); font-weight:650; }.library-nav em { min-width:20px; padding:1px 5px; border-radius:9px; color:var(--sage-review-strong); background:var(--sage-review-bg); text-align:center; font-size:10px; font-style:normal; }.page-tree { min-height:0; flex:1; overflow:auto; padding:8px; }.tree-heading { display:flex; justify-content:space-between; padding:5px 7px 7px; color:var(--sage-text-muted); font-size:11px; }.page-tree button { display:grid; grid-template-columns:17px minmax(0,1fr); align-items:center; gap:6px; width:100%; min-height:43px; padding:5px 7px; border:0; border-radius:var(--sage-radius); color:var(--sage-text-secondary); text-align:left; background:transparent; }.page-tree button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.page-tree button>span,.page-tree strong,.page-tree small { display:block; min-width:0; }.page-tree strong { overflow:hidden; font-size:var(--sage-font-xs); text-overflow:ellipsis; white-space:nowrap; }.page-tree small { margin-top:1px; color:var(--sage-text-muted); font-size:10px; }.page-tree>p { color:var(--sage-text-muted); font-size:var(--sage-font-xs); text-align:center; }.library-import { display:flex; align-items:center; gap:7px; min-height:40px; margin:8px 12px 12px; padding:0 10px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); }.library-import:hover { color:var(--sage-brand-strong); border-color:var(--sage-brand); }
.knowledge-stage { min-width:0; min-height:0; height:100%; overflow:hidden; background:var(--sage-surface); }.stage-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:50px; padding:0 14px; border-bottom:1px solid var(--sage-border); }.stage-tabs { display:flex; align-self:stretch; min-width:0; }.stage-tabs button { min-height:49px; padding:0 12px; border:0; border-bottom:2px solid transparent; color:var(--sage-text-muted); background:transparent; font-size:var(--sage-font-sm); white-space:nowrap; }.stage-tabs button.active { border-color:var(--sage-brand); color:var(--sage-text); font-weight:650; }.graph-filters,.graph-filters label,.graph-filters summary { display:flex; align-items:center; }.graph-filters { gap:6px; }.graph-filters>label,.graph-filters summary { gap:5px; min-height:32px; padding:0 8px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); font-size:var(--sage-font-xs); }.graph-filters select { border:0; outline:0; color:inherit; background:transparent; }.graph-filters details { position:relative; }.graph-filters summary { cursor:pointer; list-style:none; }.kind-menu { position:absolute; z-index:15; top:38px; right:0; display:grid; width:150px; padding:8px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface); box-shadow:var(--sage-shadow-sm); }.kind-menu label { min-height:30px; border:0; padding:0 4px; }.kind-menu input { accent-color:var(--sage-brand); }
.graph-stage { position:relative; display:grid; grid-template-rows:60px minmax(0,1fr); height:calc(100% - 50px); min-height:0; }.graph-heading { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:0 18px; border-bottom:1px solid var(--sage-border); }.graph-heading h1,.list-stage h1 { margin:0; font-size:18px; letter-spacing:0; }.graph-heading>div>span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.graph-legend { display:flex; gap:10px; color:var(--sage-text-muted); font-size:11px; }.graph-legend span { display:flex; align-items:center; gap:4px; }.graph-legend i { width:7px; height:7px; border-radius:50%; background:#3b82f6; }.graph-legend i.concept { background:#8b5cf6; }.graph-legend i.source { background:#f59e42; }.graph-legend i.tool { background:#22a6b3; }
.stage-state { display:flex; align-items:center; justify-content:center; flex-direction:column; gap:7px; height:100%; color:var(--sage-text-muted); text-align:center; }.stage-state strong { color:var(--sage-text); font-size:var(--sage-font-md); }.stage-state span { max-width:440px; font-size:var(--sage-font-sm); }.stage-state.empty { min-height:260px; padding:24px; }.stage-state.empty .primary-button { margin-top:8px; }.list-stage { height:calc(100% - 50px); min-height:0; overflow:auto; padding:22px 24px 42px; }.list-stage>header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding-bottom:18px; border-bottom:1px solid var(--sage-border); }.list-stage>header p { margin:4px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-sm); }.list-stage>header>strong { color:var(--sage-text-muted); font-size:24px; }.wiki-list article { display:grid; grid-template-columns:minmax(0,1fr) auto 34px; align-items:center; gap:16px; min-height:68px; border-bottom:1px solid var(--sage-border); }.wiki-main { display:grid; grid-template-columns:22px minmax(0,1fr); align-items:center; gap:9px; min-width:0; padding:7px 0; border:0; color:var(--sage-text-secondary); text-align:left; background:transparent; }.wiki-main span,.wiki-main strong,.wiki-main code { display:block; min-width:0; }.wiki-main strong { overflow:hidden; color:var(--sage-text); font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.wiki-main code { overflow:hidden; margin-top:3px; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }.wiki-list article>div { display:flex; flex-direction:column; align-items:flex-end; color:var(--sage-text-muted); font-size:11px; }.empty-row { padding:28px 0; color:var(--sage-text-muted); font-size:var(--sage-font-sm); text-align:center; }
.index-line,.migration-line { display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:11px; margin:16px 0; padding:12px 14px; border-left:3px solid var(--sage-source); background:var(--sage-source-bg); }.index-line>span,.index-line strong,.index-line small,.migration-line>span,.migration-line strong,.migration-line small { display:block; min-width:0; }.index-line small,.migration-line small { margin-top:2px; color:var(--sage-text-muted); font-size:11px; }.index-line code { color:var(--sage-text-muted); font-size:10px; }.projection-status { display:flex; flex-wrap:wrap; gap:8px; margin:-6px 0 8px; }.projection-status span { display:inline-flex; align-items:center; gap:4px; min-height:28px; padding:0 8px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); color:var(--sage-text-muted); background:var(--sage-surface-muted); font-size:10px; }.projection-status strong { color:var(--sage-text-secondary); }.projection-status span[data-stale="true"] { border-color:var(--sage-review); color:var(--sage-review-strong); background:var(--sage-review-bg); }.inline-warning,.inline-success { color:var(--sage-review-strong); font-size:var(--sage-font-sm); }.inline-success { color:var(--sage-success); }.job-list>article { padding:15px 0; border-bottom:1px solid var(--sage-border); }.job-list article>header,.job-list article>footer { display:flex; align-items:center; justify-content:space-between; gap:12px; }.job-list article>header span,.job-list article>header strong,.job-list article>header small { display:block; min-width:0; }.job-list article>header strong { font-size:var(--sage-font-sm); }.job-list article>header small,.job-list article>header code,.job-list article>footer { color:var(--sage-text-muted); font-size:11px; }.job-progress { height:5px; margin:10px 0; overflow:hidden; border-radius:3px; background:var(--sage-border); }.job-progress i { display:block; height:100%; background:var(--sage-brand); }.job-list article>footer { justify-content:flex-start; }.job-list article>footer button { display:inline-flex; align-items:center; gap:4px; margin-left:auto; border:0; color:var(--sage-danger); background:transparent; }.failed-items { display:grid; gap:6px; margin-top:8px; }.failed-items button { display:flex; align-items:flex-start; gap:7px; width:100%; padding:8px; border:1px solid var(--sage-danger); border-radius:var(--sage-radius-sm); color:var(--sage-danger); text-align:left; background:var(--sage-danger-bg); font-size:11px; }.failed-items button span,.failed-items button strong,.failed-items button small { display:block; min-width:0; }.failed-items button strong { overflow-wrap:anywhere; }.failed-items button small { margin-top:2px; color:var(--sage-text-muted); line-height:1.4; }
.attention-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:28px; margin-top:18px; }.attention-grid section>h2 { display:flex; justify-content:space-between; margin:0 0 8px; font-size:var(--sage-font-md); }.attention-grid section>h2 span { color:var(--sage-text-muted); }.proposal-row,.insight-row { border-bottom:1px solid var(--sage-border); padding:12px 0; }.proposal-row>header { display:flex; justify-content:space-between; gap:10px; }.proposal-row>header span,.proposal-row strong,.proposal-row code { display:block; min-width:0; }.proposal-row strong { font-size:var(--sage-font-sm); }.proposal-row code { margin-top:3px; color:var(--sage-text-muted); font-size:10px; }.proposal-row em { color:var(--sage-review-strong); font-size:10px; font-style:normal; }.proposal-row pre { max-height:130px; overflow:auto; margin:9px 0; padding:9px; border:1px solid var(--sage-border); color:var(--sage-text-secondary); background:var(--sage-surface-muted); font-family:var(--sage-font-mono); font-size:10px; white-space:pre-wrap; }.proposal-row footer { display:flex; justify-content:flex-end; gap:7px; }.proposal-row footer button,.auto-applied button { display:inline-flex; align-items:center; gap:4px; min-height:30px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); font-size:var(--sage-font-xs); }.proposal-row footer .approve { border-color:var(--sage-brand); color:var(--sage-brand-strong); }.proposal-row footer .reject { color:var(--sage-danger); }.insight-row { display:grid; grid-template-columns:18px minmax(0,1fr); color:var(--sage-source); }.insight-row[data-severity="high"] { color:var(--sage-danger); }.insight-row strong { display:block; color:var(--sage-text); font-size:var(--sage-font-sm); }.insight-row p { margin:3px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-xs); line-height:1.5; }.auto-applied { margin-top:26px; border-top:1px solid var(--sage-border); }.auto-applied summary { padding:14px 0; color:var(--sage-text-secondary); cursor:pointer; font-size:var(--sage-font-sm); }.auto-applied article { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:54px; border-top:1px solid var(--sage-border); }.auto-applied article span,.auto-applied strong,.auto-applied code { display:block; min-width:0; }.auto-applied strong { font-size:var(--sage-font-sm); }.auto-applied code { color:var(--sage-text-muted); font-size:10px; }
.workspace-footer { display:flex; align-items:center; gap:18px; min-width:0; padding:0 14px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); background:var(--sage-surface); font-size:11px; }.workspace-footer span { display:flex; align-items:center; gap:5px; white-space:nowrap; }.workspace-footer i { width:7px; height:7px; border-radius:50%; background:var(--sage-success); }.workspace-footer i.active { background:var(--sage-review); }.footer-spacer { flex:1; }
.panel-backdrop,.modal-backdrop { position:fixed; z-index:54; inset:0; background:var(--sage-overlay); }.modal-backdrop { z-index:80; display:grid; place-items:center; padding:20px; }.import-dialog { width:min(680px,100%); max-height:min(720px,calc(100dvh - 40px)); overflow:auto; border:1px solid var(--sage-border); border-radius:var(--sage-radius-lg); background:var(--sage-surface); box-shadow:var(--sage-shadow-drawer); }.import-dialog>header,.import-dialog>header>div { display:flex; align-items:flex-start; }.import-dialog>header { justify-content:space-between; gap:14px; padding:18px 20px; border-bottom:1px solid var(--sage-border); }.import-dialog>header>div { gap:11px; }.import-dialog>header>div>span { display:grid; place-items:center; width:34px; height:34px; border-radius:var(--sage-radius); color:var(--sage-brand-strong); background:var(--sage-brand-bg); }.import-dialog h2 { margin:0; font-size:18px; }.import-dialog header p { margin:3px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.field-label { display:grid; gap:6px; margin:16px 20px; color:var(--sage-text-secondary); font-size:var(--sage-font-xs); }.field-label select,.import-options input { min-width:0; height:38px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); padding:0 10px; outline:0; color:var(--sage-text); background:var(--sage-surface); }.field-label select:focus,.import-options input:focus { border-color:var(--sage-source); }.import-options { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; padding:0 20px 20px; }.import-options form { display:grid; grid-template-rows:auto auto auto auto; align-content:start; gap:10px; padding:15px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); }.import-options form>svg { color:var(--sage-source); }.import-options strong { font-size:var(--sage-font-md); }.import-options p { min-height:38px; margin:3px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-xs); line-height:1.5; }.import-dialog>footer { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:12px 20px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); background:var(--sage-surface-muted); font-size:11px; }.import-dialog>footer span { display:flex; align-items:center; gap:5px; }.import-dialog>footer p { margin:0; text-align:right; }
.sync-preview { display:grid; gap:9px; min-width:0; padding:11px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface-muted); }.sync-preview>header { display:flex; align-items:center; justify-content:space-between; gap:8px; }.sync-preview>header strong { display:flex; align-items:center; gap:5px; font-size:var(--sage-font-sm); }.sync-preview>header code,.sync-preview>small { color:var(--sage-text-muted); font-size:10px; }.sync-counts { display:flex; flex-wrap:wrap; gap:6px; }.sync-counts span { padding:3px 6px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); color:var(--sage-text-secondary); background:var(--sage-surface); font-size:10px; }.sync-preview ul { max-height:150px; overflow:auto; margin:0; padding:0; list-style:none; }.sync-preview li { display:grid; grid-template-columns:34px minmax(0,1fr); align-items:center; gap:7px; min-height:28px; border-top:1px solid var(--sage-border); }.sync-preview li em { color:var(--sage-source); font-size:10px; font-style:normal; }.sync-preview li em[data-kind="deleted"] { color:var(--sage-danger); }.sync-preview li em[data-kind="modified"] { color:var(--sage-review-strong); }.sync-preview li code { overflow:hidden; color:var(--sage-text-secondary); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }.sync-preview>.primary-button { width:100%; }
.spinning { animation:spin .9s linear infinite; }@keyframes spin { to { transform:rotate(360deg); } }
@media (max-width:1359px) {
  .workspace-body,.workspace-body.is-loading { grid-template-columns:minmax(0,1fr); }.knowledge-library.compact { position:absolute; z-index:55; inset:0 auto 0 0; width:min(280px,calc(100vw - 40px)); display:none; box-shadow:var(--sage-shadow-drawer); }.knowledge-library.compact.open { display:flex; }
}
@media (max-width:899px) {
  .knowledge-workspace { grid-template-rows:58px minmax(0,1fr) 38px; }.workspace-header { padding-left:56px; }.workspace-body,.workspace-body.is-loading { display:block; }.knowledge-harness,.knowledge-stage { height:100%; }.header-actions code { display:none; }.workspace-footer span:nth-child(3),.workspace-footer span:nth-child(4) { display:none; }.list-stage { padding:18px 16px 36px; }.attention-grid { grid-template-columns:1fr; }.graph-stage { height:calc(100% - 50px); }
}
@media (max-width:620px) {
  .workspace-header { padding-right:9px; }.title-mark { display:none; }.workspace-title strong { max-width:150px; }.header-actions .secondary-button { width:34px; padding:0; }.header-actions .secondary-button span { display:none; }.header-actions .primary-button span { display:none; }.header-actions .primary-button { width:34px; padding:0; }.stage-toolbar { padding:0 8px; overflow-x:auto; }.stage-tabs button { padding:0 8px; font-size:var(--sage-font-xs); }.graph-filters>label svg,.graph-filters details { display:none; }.graph-heading { min-height:70px; padding:0 12px; }.graph-legend { display:none; }.list-stage>header p { display:none; }.wiki-list article { grid-template-columns:minmax(0,1fr) 34px; }.wiki-list article>div { display:none; }.index-line,.migration-line { grid-template-columns:auto minmax(0,1fr); }.index-line code,.migration-line button { grid-column:2; justify-self:start; }.import-options { grid-template-columns:1fr; }.import-dialog>footer { display:block; }.import-dialog>footer p { margin-top:5px; text-align:left; }.workspace-footer { gap:10px; }.workspace-footer span:nth-child(2) { display:none; }
}
@media (prefers-reduced-motion:reduce) { .spinning { animation:none; } }
</style>
