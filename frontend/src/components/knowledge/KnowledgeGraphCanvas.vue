<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Focus, Minus, Plus } from 'lucide-vue-next'
import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import type Sigma from 'sigma'
import type {
  KnowledgeGraph,
  KnowledgeGraphCommunities,
  KnowledgeGraphNode,
  KnowledgeGraphNodeKind,
} from '../../types/api'

const props = defineProps<{
  graph: KnowledgeGraph
  communities: KnowledgeGraphCommunities | null
  selectedNodeId: string | null
  colorMode: 'type' | 'community'
  visibleKinds: KnowledgeGraphNodeKind[]
  query: string
}>()

const emit = defineEmits<{
  select: [nodeId: string | null]
}>()

const container = ref<HTMLElement | null>(null)
const mobile = ref(window.innerWidth < 680)
const rendererError = ref('')
const themeRevision = ref(0)
let renderer: Sigma | null = null
let sigmaGraph: Graph | null = null
let themeObserver: MutationObserver | null = null
let renderRequest = 0

const typeColors: Record<KnowledgeGraphNodeKind, string> = {
  page: '#3b82f6',
  source: '#f59e42',
  project: '#16a085',
  concept: '#8b5cf6',
  decision: '#ef6b63',
  tool: '#22a6b3',
}

const communityPalette = [
  '#3b82f6',
  '#14b8a6',
  '#8b5cf6',
  '#f59e42',
  '#ef6b63',
  '#22a6b3',
  '#6f7bf7',
  '#57a773',
  '#d66ba0',
  '#829356',
]

const communityByNode = computed(() => new Map(
  props.communities?.node_metrics.map((item) => [item.node_id, item.community_id]) ?? [],
))
const degreeByNode = computed(() => new Map(
  props.communities?.node_metrics.map((item) => [item.node_id, item.degree]) ?? [],
))
const communityColor = computed(() => {
  const ids = [...new Set(props.communities?.communities.map((item) => item.community_id) ?? [])]
  ids.sort()
  return new Map(ids.map((id, index) => [id, communityPalette[index % communityPalette.length]]))
})
const visibleSet = computed(() => new Set(props.visibleKinds))
const normalizedQuery = computed(() => props.query.trim().toLocaleLowerCase())
const mobileNodes = computed(() => props.graph.nodes
  .filter(isNodeVisible)
  .sort((left, right) => {
    const degree = (degreeByNode.value.get(right.node_id) ?? 0)
      - (degreeByNode.value.get(left.node_id) ?? 0)
    return degree || left.label.localeCompare(right.label, 'zh-CN')
  })
  .slice(0, 80))

function isNodeVisible(node: KnowledgeGraphNode) {
  if (!visibleSet.value.has(node.kind)) return false
  const query = normalizedQuery.value
  return !query || node.label.toLocaleLowerCase().includes(query)
}

function nodeColor(node: KnowledgeGraphNode) {
  if (props.colorMode === 'type') return typeColors[node.kind]
  const communityId = communityByNode.value.get(node.node_id)
  return (communityId && communityColor.value.get(communityId)) || typeColors[node.kind]
}

function stablePosition(nodeId: string, index: number, total: number) {
  let hash = 2166136261
  for (let offset = 0; offset < nodeId.length; offset += 1) {
    hash ^= nodeId.charCodeAt(offset)
    hash = Math.imul(hash, 16777619)
  }
  const angle = ((hash >>> 0) / 0xffffffff) * Math.PI * 2
  const ring = 1 + (index % Math.max(4, Math.ceil(Math.sqrt(total))))
  return { x: Math.cos(angle) * ring, y: Math.sin(angle) * ring }
}

function cachedPositions() {
  try {
    const raw = localStorage.getItem(`sage.knowledge.graph.layout.${props.graph.snapshot.graph_revision}`)
    if (!raw) return new Map<string, { x: number; y: number }>()
    return new Map(Object.entries(JSON.parse(raw) as Record<string, { x: number; y: number }>))
  } catch {
    return new Map<string, { x: number; y: number }>()
  }
}

function savePositions(graph: Graph) {
  const positions: Record<string, { x: number; y: number }> = {}
  graph.forEachNode((nodeId, attributes) => {
    positions[nodeId] = { x: Number(attributes.x), y: Number(attributes.y) }
  })
  try {
    localStorage.setItem(
      `sage.knowledge.graph.layout.${props.graph.snapshot.graph_revision}`,
      JSON.stringify(positions),
    )
  } catch {
    // Layout persistence is an enhancement; private browsing must not break the graph.
  }
}

async function mountRenderer() {
  const requestId = ++renderRequest
  renderer?.kill()
  renderer = null
  sigmaGraph = null
  rendererError.value = ''
  if (mobile.value || !container.value) return
  await nextTick()
  const { default: SigmaRenderer } = await import('sigma')
  if (requestId !== renderRequest || !container.value) return
  const graph = new Graph({ multi: true, type: 'undirected', allowSelfLoops: false })
  const positions = cachedPositions()
  const rootStyles = getComputedStyle(document.documentElement)
  const labelColor = rootStyles.getPropertyValue('--sage-text').trim() || '#202124'
  const mutedEdgeColor = rootStyles.getPropertyValue('--sage-border-strong').trim() || '#c7c9ce'
  const linkedEdgeColor = rootStyles.getPropertyValue('--sage-source').trim() || '#3478b9'
  const labelFont = rootStyles.getPropertyValue('--sage-font-sans').trim() || 'sans-serif'
  props.graph.nodes.forEach((node, index) => {
    const position = positions.get(node.node_id)
      ?? stablePosition(node.node_id, index, props.graph.nodes.length)
    const degree = degreeByNode.value.get(node.node_id) ?? 0
    graph.addNode(node.node_id, {
      ...position,
      label: node.label,
      color: nodeColor(node),
      size: node.kind === 'source' ? 3 : Math.min(13, 5 + Math.sqrt(degree) * 1.7),
      hidden: !isNodeVisible(node),
      forceLabel: node.node_id === props.selectedNodeId,
      zIndex: node.node_id === props.selectedNodeId ? 2 : 1,
    })
  })
  for (const edge of props.graph.edges) {
    if (!graph.hasNode(edge.source_node_id) || !graph.hasNode(edge.target_node_id)) continue
    graph.addEdgeWithKey(edge.edge_id, edge.source_node_id, edge.target_node_id, {
      color: edge.kind === 'WIKILINK' ? linkedEdgeColor : mutedEdgeColor,
      size: edge.kind === 'WIKILINK' ? 1.2 : 0.7,
      hidden: graph.getNodeAttribute(edge.source_node_id, 'hidden')
        || graph.getNodeAttribute(edge.target_node_id, 'hidden'),
      type: 'line',
      zIndex: 0,
    })
  }
  if (positions.size !== props.graph.nodes.length && graph.order > 1 && graph.size > 0) {
    forceAtlas2.assign(graph, {
      iterations: Math.min(180, 60 + graph.order),
      settings: {
        gravity: 1,
        scalingRatio: 8,
        strongGravityMode: false,
        slowDown: 5,
        barnesHutOptimize: graph.order > 100,
      },
    })
    savePositions(graph)
  }
  try {
    renderer = new SigmaRenderer(graph, container.value, {
      allowInvalidContainer: false,
      defaultEdgeColor: mutedEdgeColor,
      defaultEdgeType: 'line',
      labelColor: { color: labelColor },
      labelDensity: 0.08,
      labelFont,
      labelGridCellSize: 110,
      labelRenderedSizeThreshold: 8,
      renderEdgeLabels: false,
      zIndex: true,
    })
    sigmaGraph = graph
    renderer.on('clickNode', ({ node }) => emit('select', node))
    renderer.on('clickStage', () => emit('select', null))
  } catch (reason) {
    rendererError.value = reason instanceof Error ? reason.message : '当前浏览器无法渲染图谱'
  }
}

function requestRenderer() {
  void mountRenderer().catch((reason: unknown) => {
    rendererError.value = reason instanceof Error ? reason.message : '当前浏览器无法渲染图谱'
  })
}

function updateSelection() {
  if (!sigmaGraph || !renderer) return
  sigmaGraph.forEachNode((nodeId) => {
    const selected = nodeId === props.selectedNodeId
    sigmaGraph?.setNodeAttribute(nodeId, 'forceLabel', selected)
    sigmaGraph?.setNodeAttribute(nodeId, 'zIndex', selected ? 2 : 1)
  })
  renderer.refresh()
  if (props.selectedNodeId && sigmaGraph.hasNode(props.selectedNodeId)) {
    const position = renderer.getNodeDisplayData(props.selectedNodeId)
    if (position) renderer.getCamera().animate(position, { duration: 260 })
  }
}

function zoom(direction: 'in' | 'out') {
  if (!renderer) return
  const camera = renderer.getCamera()
  if (direction === 'in') camera.animatedZoom({ duration: 180 })
  else camera.animatedUnzoom({ duration: 180 })
}

function resetCamera() {
  renderer?.getCamera().animatedReset({ duration: 240 })
}

function updateViewport() {
  const next = window.innerWidth < 680
  if (next === mobile.value) return
  mobile.value = next
  requestRenderer()
}

watch(
  () => [
    props.graph.snapshot.graph_revision,
    props.colorMode,
    props.visibleKinds.join(','),
    props.query,
    props.communities?.analysis.analysis_revision,
    themeRevision.value,
  ],
  requestRenderer,
)
watch(() => props.selectedNodeId, updateSelection)

onMounted(() => {
  window.addEventListener('resize', updateViewport)
  themeObserver = new MutationObserver(() => {
    themeRevision.value += 1
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  requestRenderer()
})
onBeforeUnmount(() => {
  renderRequest += 1
  window.removeEventListener('resize', updateViewport)
  themeObserver?.disconnect()
  renderer?.kill()
})
</script>

<template>
  <div class="graph-surface">
    <div v-if="!mobile" ref="container" class="sigma-container" aria-label="本地知识图谱"></div>
    <div v-if="!mobile" class="graph-controls" aria-label="图谱缩放">
      <button type="button" title="放大" aria-label="放大图谱" @click="zoom('in')"><Plus :size="17" /></button>
      <button type="button" title="缩小" aria-label="缩小图谱" @click="zoom('out')"><Minus :size="17" /></button>
      <button type="button" title="居中" aria-label="重置图谱视图" @click="resetCamera"><Focus :size="17" /></button>
    </div>
    <p v-if="rendererError && !mobile" class="graph-error" role="status">{{ rendererError }}</p>
    <div v-if="mobile || rendererError" class="mobile-node-list" aria-label="知识节点列表">
      <button
        v-for="node in mobileNodes"
        :key="node.node_id"
        type="button"
        :class="{ active: node.node_id === selectedNodeId }"
        @click="emit('select', node.node_id)"
      >
        <i :style="{ background: nodeColor(node) }"></i>
        <span><strong>{{ node.label }}</strong><small>{{ node.kind }} · {{ degreeByNode.get(node.node_id) ?? 0 }} 条连接</small></span>
      </button>
      <p v-if="mobileNodes.length === 0">没有符合筛选条件的节点。</p>
    </div>
  </div>
</template>

<style scoped>
.graph-surface { position:relative; min-width:0; min-height:0; height:100%; overflow:hidden; background:var(--sage-surface); }
.sigma-container { position:absolute; inset:0; }.graph-controls { position:absolute; right:16px; bottom:16px; display:flex; flex-direction:column; overflow:hidden; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:var(--sage-surface); box-shadow:var(--sage-shadow-sm); }.graph-controls button { display:grid; place-items:center; width:36px; height:36px; padding:0; border:0; border-bottom:1px solid var(--sage-border); color:var(--sage-text-secondary); background:transparent; }.graph-controls button:last-child { border-bottom:0; }.graph-controls button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.graph-error { position:absolute; top:16px; left:50%; margin:0; padding:8px 11px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-danger); background:var(--sage-surface); transform:translateX(-50%); }
.mobile-node-list { height:100%; overflow:auto; padding:8px 14px 24px; }.mobile-node-list button { display:grid; grid-template-columns:10px minmax(0,1fr); align-items:center; gap:11px; width:100%; min-height:58px; padding:8px 4px; border:0; border-bottom:1px solid var(--sage-border); text-align:left; color:var(--sage-text); background:transparent; }.mobile-node-list button.active { background:var(--sage-source-bg); }.mobile-node-list i { width:9px; height:9px; border-radius:50%; }.mobile-node-list span,.mobile-node-list strong,.mobile-node-list small { display:block; min-width:0; }.mobile-node-list strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:var(--sage-font-md); }.mobile-node-list small { margin-top:3px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.mobile-node-list>p { color:var(--sage-text-muted); text-align:center; }
@media (prefers-reduced-motion:reduce) { .graph-controls button { transition:none; } }
</style>
