<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ArrowUpRight, BookOpenText, Link2, Network, Target, X } from 'lucide-vue-next'
import type {
  KnowledgeGoalAlignment,
  KnowledgeGraphCommunity,
  KnowledgeGraphInsight,
  KnowledgeGraphNeighborhood,
  KnowledgeGraphNode,
  KnowledgeLearningGoal,
  KnowledgePage,
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
const tab = ref<'overview' | 'evidence' | 'relations'>('overview')
watch(() => props.node?.node_id, () => { tab.value = 'overview' })

const community = computed(() => props.communities.find(
  (item) => item.community_id === props.communityId,
) ?? null)
const nodeInsights = computed(() => props.node
  ? props.insights.filter((item) => item.node_id === props.node?.node_id)
  : [])
const neighborNodes = computed(() => props.neighborhood?.nodes.filter(
  (item) => item.node_id !== props.node?.node_id,
) ?? [])
const evidence = computed(() => {
  const seen = new Set<string>()
  return (props.neighborhood?.edges ?? []).flatMap((edge) => edge.evidence).filter((item) => {
    if (seen.has(item.citation_id)) return false
    seen.add(item.citation_id)
    return true
  })
})

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
        <span>{{ node ? node.kind : '学习目标' }}</span>
        <h2>{{ node?.label || goal?.title || '本地知识分析' }}</h2>
      </div>
      <button ref="closeButton" type="button" aria-label="关闭知识详情" title="关闭" @click="emit('close')">
        <X :size="18" />
      </button>
    </header>

    <template v-if="node">
      <nav class="inspector-tabs" aria-label="详情分类">
        <button type="button" :class="{ active: tab === 'overview' }" @click="tab = 'overview'">概览</button>
        <button type="button" :class="{ active: tab === 'evidence' }" @click="tab = 'evidence'">证据 {{ evidence.length }}</button>
        <button type="button" :class="{ active: tab === 'relations' }" @click="tab = 'relations'">连接 {{ neighborhood?.edges.length ?? 0 }}</button>
      </nav>

      <div v-if="loading" class="inspector-state" role="status">正在读取节点详情...</div>
      <div v-else-if="tab === 'overview'" class="inspector-body">
        <section v-if="community" aria-labelledby="community-title">
          <div class="section-title"><Network :size="16" /><h3 id="community-title">所属社区</h3></div>
          <strong>{{ community.label }}</strong>
          <p>{{ community.node_count }} 个节点 · {{ community.edge_count }} 条内部边 · cohesion {{ community.cohesion.toFixed(2) }}</p>
        </section>
        <section v-if="page" aria-labelledby="page-title">
          <div class="section-title"><BookOpenText :size="16" /><h3 id="page-title">Wiki 页面</h3></div>
          <code>{{ page.path }}</code>
          <p>当前 revision {{ page.current_revision.slice(0, 18) }} · 共 {{ page.revisions.length }} 个版本</p>
        </section>
        <section v-if="nodeInsights.length" aria-labelledby="insight-title">
          <div class="section-title"><Target :size="16" /><h3 id="insight-title">本地洞察</h3></div>
          <article v-for="insight in nodeInsights" :key="insight.insight_id" :data-severity="insight.severity">
            <strong>{{ insight.title }}</strong><p>{{ insight.description }}</p>
          </article>
        </section>
        <section aria-labelledby="revision-title">
          <div class="section-title"><Link2 :size="16" /><h3 id="revision-title">版本绑定</h3></div>
          <dl>
            <div><dt>Page</dt><dd>{{ node.page_revision?.slice(0, 20) || '无' }}</dd></div>
            <div><dt>Source</dt><dd>{{ node.source_revision?.slice(0, 20) || '无' }}</dd></div>
          </dl>
        </section>
      </div>

      <div v-else-if="tab === 'evidence'" class="inspector-body evidence-list">
        <p v-if="!evidence.length" class="inspector-state">当前邻域没有可展示证据。</p>
        <article v-for="item in evidence" :key="item.citation_id">
          <strong>{{ item.citation_id }}</strong>
          <code>{{ item.chunk_id }}</code>
          <span>page {{ item.page_revision.slice(0, 17) }}</span>
          <span>source {{ item.source_revision.slice(0, 17) }}</span>
        </article>
      </div>

      <div v-else class="inspector-body relation-list">
        <p v-if="!neighborNodes.length" class="inspector-state">当前节点没有一跳关系。</p>
        <button
          v-for="neighbor in neighborNodes"
          :key="neighbor.node_id"
          type="button"
          @click="emit('select', neighbor.node_id)"
        >
          <span><strong>{{ neighbor.label }}</strong><small>{{ neighbor.kind }}</small></span>
          <ArrowUpRight :size="15" />
        </button>
      </div>
    </template>

    <div v-else class="inspector-body goal-inspector">
      <section v-if="goal">
        <div class="section-title"><Target :size="16" /><h3>当前目标</h3></div>
        <p>{{ goal.description }}</p>
        <code>{{ goal.goal_revision }}</code>
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
.knowledge-inspector { display:flex; flex-direction:column; min-width:0; min-height:0; border-left:1px solid var(--sage-border); background:var(--sage-surface); }.inspector-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; min-height:72px; padding:14px 16px; border-bottom:1px solid var(--sage-border); }.inspector-heading>div { min-width:0; }.inspector-heading span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); text-transform:uppercase; }.inspector-heading h2 { overflow:hidden; margin:3px 0 0; font-size:16px; line-height:1.35; text-overflow:ellipsis; white-space:nowrap; }.inspector-heading button { display:grid; place-items:center; width:32px; height:32px; flex:none; padding:0; border:0; border-radius:var(--sage-radius); color:var(--sage-text-muted); background:transparent; }.inspector-heading button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }
.inspector-tabs { display:grid; grid-template-columns:repeat(3,1fr); min-height:44px; border-bottom:1px solid var(--sage-border); }.inspector-tabs button { border:0; border-bottom:2px solid transparent; color:var(--sage-text-muted); background:transparent; font-size:var(--sage-font-sm); }.inspector-tabs button.active { border-color:var(--sage-source); color:var(--sage-text); font-weight:650; }
.inspector-body { min-height:0; overflow:auto; padding:2px 17px 24px; }.inspector-body section { padding:17px 0; border-bottom:1px solid var(--sage-border); }.section-title { display:flex; align-items:center; gap:7px; margin-bottom:10px; color:var(--sage-text-secondary); }.section-title h3 { margin:0; font-size:var(--sage-font-sm); }.inspector-body section>strong { font-size:var(--sage-font-md); }.inspector-body p { margin:5px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-sm); line-height:1.55; }.inspector-body code { display:block; overflow:hidden; color:var(--sage-text-secondary); font-family:var(--sage-font-mono); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.inspector-body article[data-severity] { padding:9px 10px; border-left:3px solid var(--sage-source); background:var(--sage-surface-muted); }.inspector-body article[data-severity="high"] { border-color:var(--sage-coral); }.inspector-body article[data-severity="medium"] { border-color:var(--sage-review); }.inspector-body article+article { margin-top:7px; }.inspector-body article strong { font-size:var(--sage-font-sm); }.inspector-body article p { font-size:var(--sage-font-xs); }dl { margin:0; }dl div { display:flex; justify-content:space-between; gap:12px; min-height:30px; }dt { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }dd { overflow:hidden; margin:0; font-family:var(--sage-font-mono); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.inspector-state { padding:24px 0; color:var(--sage-text-muted); text-align:center; }.evidence-list article { padding:13px 0; border-bottom:1px solid var(--sage-border); }.evidence-list strong,.evidence-list span { display:block; }.evidence-list strong { overflow:hidden; font-family:var(--sage-font-mono); font-size:11px; text-overflow:ellipsis; }.evidence-list code,.evidence-list span { margin-top:4px; color:var(--sage-text-muted); font-size:11px; }.relation-list button { display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%; min-height:58px; padding:7px 2px; border:0; border-bottom:1px solid var(--sage-border); text-align:left; color:var(--sage-text); background:transparent; }.relation-list button:hover { color:var(--sage-source); }.relation-list span,.relation-list strong,.relation-list small { display:block; min-width:0; }.relation-list strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:var(--sage-font-sm); }.relation-list small { margin-top:3px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
.goal-inspector { padding-top:4px; }.alignment-row { padding:10px 0 !important; border:0 !important; background:transparent !important; }.alignment-row>div:first-child { display:flex; justify-content:space-between; gap:12px; }.alignment-row span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.coverage-track { height:5px; margin-top:7px; overflow:hidden; border-radius:3px; background:var(--sage-border); }.coverage-track i { display:block; height:100%; background:var(--sage-brand); }.alignment-row p { font-size:11px; }
.knowledge-inspector.compact { position:fixed; z-index:60; inset:52px 0 0 auto; width:min(380px,100vw); box-shadow:var(--sage-shadow-drawer); }
@media (max-width:600px) { .knowledge-inspector.compact { inset:0; width:100%; }.inspector-heading { min-height:60px; } }
</style>
