<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Focus, Minus, Plus, X } from 'lucide-vue-next'
import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import type Sigma from 'sigma'
import type {
  KnowledgeGraph,
  KnowledgeGraphCommunities,
  KnowledgeGraphNode,
  KnowledgeGraphNodeKind,
} from '../../types/api'
import {
  communitySeedPositions,
  featuredNodeIds,
  graphFocus,
} from './knowledgeGraphPresentation'

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

const surface = ref<HTMLElement | null>(null)
const container = ref<HTMLElement | null>(null)
const mobile = ref(window.innerWidth < 680)
const rendererError = ref('')
const themeRevision = ref(0)
let renderer: Sigma | null = null
let sigmaGraph: Graph | null = null
let themeObserver: MutationObserver | null = null
let resizeObserver: ResizeObserver | null = null
let renderRequest = 0
let rendererPending = false
let rendererDirty = true
let hoveredNodeId: string | null = null
let dimNodeColor = 'rgba(126, 132, 142, 0.2)'
let dimEdgeColor = 'rgba(126, 132, 142, 0.08)'
let focusEdgeColor = '#58c78b'

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

const metricByNode = computed(() => {
  const metrics = new Map(props.graph.nodes.map((node) => [node.node_id, {
    community_id: null as string | null,
    degree: 0,
    weighted_degree: 0,
    bridge_score: 0,
  }]))
  for (const edge of props.graph.edges) {
    for (const nodeId of [edge.source_node_id, edge.target_node_id]) {
      const metric = metrics.get(nodeId)
      if (!metric) continue
      metric.degree += 1
      metric.weighted_degree += edge.weight
    }
  }
  for (const metric of props.communities?.node_metrics ?? []) {
    const current = metrics.get(metric.node_id)
    if (!current) continue
    current.community_id = metric.community_id
    current.bridge_score = metric.bridge_score
  }
  return metrics
})
const communityByNode = computed(() => new Map(
  [...metricByNode.value]
    .filter(([, item]) => item.community_id)
    .map(([nodeId, item]) => [nodeId, item.community_id as string]),
))
const degreeByNode = computed(() => new Map(
  [...metricByNode.value].map(([nodeId, item]) => [nodeId, item.degree]),
))
const communityColor = computed(() => {
  const ids = [...new Set(props.communities?.communities.map((item) => item.community_id) ?? [])]
  ids.sort()
  return new Map(ids.map((id, index) => [id, communityPalette[index % communityPalette.length]]))
})
const visibleSet = computed(() => new Set(props.visibleKinds))
const normalizedQuery = computed(() => props.query.trim().toLocaleLowerCase())
const featuredLabels = computed(() => featuredNodeIds(props.graph, props.communities))
const focus = computed(() => graphFocus(props.graph, props.selectedNodeId))
const selectedNode = computed(() => props.graph.nodes.find(
  (node) => node.node_id === props.selectedNodeId,
) ?? null)
const selectedMetric = computed(() => (
  props.selectedNodeId ? metricByNode.value.get(props.selectedNodeId) : null
))
const selectedCommunity = computed(() => props.communities?.communities.find(
  (item) => item.community_id === selectedMetric.value?.community_id,
) ?? null)
const sourceCount = computed(() => props.graph.nodes.filter((node) => node.kind === 'source').length)
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

function layoutCacheKey() {
  const analysisRevision = props.communities?.analysis.analysis_revision ?? 'unclustered'
  return `sage.knowledge.graph.layout.v4.${props.graph.snapshot.graph_revision}.${analysisRevision}`
}

function cachedPositions() {
  try {
    const raw = localStorage.getItem(layoutCacheKey())
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
      layoutCacheKey(),
      JSON.stringify(positions),
    )
  } catch {
    // Layout persistence is an enhancement; private browsing must not break the graph.
  }
}

function applyPresentation() {
  if (!sigmaGraph) return
  const selectedNodeId = props.selectedNodeId
  const focusValue = focus.value

  sigmaGraph.forEachNode((nodeId, attributes) => {
    const selected = nodeId === selectedNodeId
    const connected = focusValue.connectedNodeIds.has(nodeId)
    const hovered = nodeId === hoveredNodeId
    const baseColor = String(attributes.baseColor)
    const baseSize = Number(attributes.baseSize)
    sigmaGraph?.setNodeAttribute(
      nodeId,
      'color',
      selectedNodeId && !selected && !connected ? dimNodeColor : baseColor,
    )
    sigmaGraph?.setNodeAttribute(
      nodeId,
      'size',
      selected ? baseSize * 1.35 : connected || hovered ? baseSize * 1.08 : baseSize,
    )
    sigmaGraph?.setNodeAttribute(
      nodeId,
      'forceLabel',
      hovered || (selectedNodeId
        ? focusValue.labelNodeIds.has(nodeId)
        : featuredLabels.value.has(nodeId)),
    )
    sigmaGraph?.setNodeAttribute(nodeId, 'highlighted', false)
    sigmaGraph?.setNodeAttribute(nodeId, 'zIndex', selected ? 5 : connected || hovered ? 3 : 1)
  })

  sigmaGraph.forEachEdge((edgeId, attributes) => {
    const focused = focusValue.focusedEdgeIds.has(edgeId)
    sigmaGraph?.setEdgeAttribute(
      edgeId,
      'hidden',
      Boolean(attributes.baseHidden) || Boolean(selectedNodeId && !focused),
    )
    sigmaGraph?.setEdgeAttribute(
      edgeId,
      'color',
      selectedNodeId ? (focused ? focusEdgeColor : dimEdgeColor) : String(attributes.baseColor),
    )
    sigmaGraph?.setEdgeAttribute(
      edgeId,
      'size',
      selectedNodeId && focused
        ? Math.max(1.35, Number(attributes.baseSize) * 1.45)
        : Number(attributes.baseSize),
    )
    sigmaGraph?.setEdgeAttribute(edgeId, 'zIndex', selectedNodeId && focused ? 2 : 0)
  })
  renderer?.refresh()
}

function hasRenderableArea() {
  if (!container.value) return false
  const bounds = container.value.getBoundingClientRect()
  return bounds.width >= 2 && bounds.height >= 2
}

function destroyRenderer() {
  renderer?.kill()
  renderer = null
  sigmaGraph = null
  hoveredNodeId = null
}

async function mountRenderer(requestId: number) {
  rendererError.value = ''
  if (mobile.value || !container.value) {
    if (mobile.value) destroyRenderer()
    return
  }
  await nextTick()
  if (requestId !== renderRequest || !hasRenderableArea()) return
  const { default: SigmaRenderer } = await import('sigma')
  if (requestId !== renderRequest || !container.value || !hasRenderableArea()) return
  destroyRenderer()
  const graph = new Graph({ multi: true, type: 'undirected', allowSelfLoops: false })
  const positions = cachedPositions()
  const seedPositions = communitySeedPositions(props.graph, props.communities)
  const rootStyles = getComputedStyle(document.documentElement)
  const labelColor = rootStyles.getPropertyValue('--sage-text').trim() || '#202124'
  const labelFont = rootStyles.getPropertyValue('--sage-font-sans').trim() || 'sans-serif'
  const brandColor = rootStyles.getPropertyValue('--sage-brand').trim() || '#58c78b'
  const darkTheme = document.documentElement.dataset.theme === 'dark'
  const mutedEdgeColor = darkTheme ? 'rgba(139, 148, 163, 0.11)' : 'rgba(113, 122, 137, 0.12)'
  const linkedEdgeColor = darkTheme ? 'rgba(111, 148, 205, 0.28)' : 'rgba(72, 114, 177, 0.27)'
  dimNodeColor = darkTheme ? 'rgba(139, 148, 163, 0.2)' : 'rgba(113, 122, 137, 0.2)'
  dimEdgeColor = darkTheme ? 'rgba(139, 148, 163, 0.08)' : 'rgba(113, 122, 137, 0.08)'
  focusEdgeColor = brandColor
  props.graph.nodes.forEach((node, index) => {
    const position = positions.get(node.node_id)
      ?? seedPositions.get(node.node_id)
      ?? { x: index, y: 0 }
    const metric = metricByNode.value.get(node.node_id)
    const weightedDegree = metric?.weighted_degree ?? metric?.degree ?? 0
    const baseColor = nodeColor(node)
    const baseSize = node.kind === 'source'
      ? 2.6
      : Math.min(9.5, 3.2 + Math.sqrt(weightedDegree) * 1.05)
    graph.addNode(node.node_id, {
      ...position,
      label: node.label,
      color: baseColor,
      baseColor,
      size: baseSize,
      baseSize,
      hidden: !isNodeVisible(node),
      forceLabel: false,
      zIndex: 1,
    })
  })
  for (const edge of props.graph.edges) {
    if (!graph.hasNode(edge.source_node_id) || !graph.hasNode(edge.target_node_id)) continue
    const baseColor = edge.kind === 'WIKILINK' ? linkedEdgeColor : mutedEdgeColor
    const baseSize = edge.kind === 'WIKILINK'
      ? Math.min(1.35, 0.8 + edge.weight * 0.13)
      : Math.min(0.9, 0.5 + edge.confidence * 0.25)
    const baseHidden = graph.getNodeAttribute(edge.source_node_id, 'hidden')
      || graph.getNodeAttribute(edge.target_node_id, 'hidden')
    graph.addEdgeWithKey(edge.edge_id, edge.source_node_id, edge.target_node_id, {
      color: baseColor,
      baseColor,
      size: baseSize,
      baseSize,
      hidden: baseHidden,
      baseHidden,
      type: 'line',
      zIndex: 0,
    })
  }
  if (
    positions.size !== props.graph.nodes.length
    && graph.order > 1
    && graph.size > 0
    && !props.communities?.communities.length
  ) {
    forceAtlas2.assign(graph, {
      iterations: Math.min(260, 90 + graph.order),
      settings: {
        edgeWeightInfluence: 0.7,
        gravity: 0.12,
        linLogMode: true,
        scalingRatio: 28,
        strongGravityMode: false,
        slowDown: 12,
        barnesHutOptimize: graph.order > 100,
        barnesHutTheta: 0.7,
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
      labelDensity: 0.06,
      labelFont,
      labelGridCellSize: 130,
      labelRenderedSizeThreshold: 12,
      renderEdgeLabels: false,
      zIndex: true,
    })
    sigmaGraph = graph
    hoveredNodeId = null
    applyPresentation()
    renderer.on('clickNode', ({ node }) => emit('select', node))
    renderer.on('clickStage', () => emit('select', null))
    renderer.on('enterNode', ({ node }) => {
      hoveredNodeId = node
      applyPresentation()
    })
    renderer.on('leaveNode', () => {
      hoveredNodeId = null
      applyPresentation()
    })
    rendererDirty = false
  } catch (reason) {
    rendererDirty = false
    rendererError.value = reason instanceof Error ? reason.message : '当前浏览器无法渲染图谱'
  }
}

function scheduleRenderer() {
  if (rendererPending) return
  const requestId = renderRequest
  rendererPending = true
  void mountRenderer(requestId)
    .catch((reason: unknown) => {
      rendererDirty = false
      rendererError.value = reason instanceof Error ? reason.message : '当前浏览器无法渲染图谱'
    })
    .finally(() => {
      rendererPending = false
      if (rendererDirty && requestId !== renderRequest && hasRenderableArea()) {
        scheduleRenderer()
      }
    })
}

function requestRenderer() {
  renderRequest += 1
  rendererDirty = true
  scheduleRenderer()
}

function updateRendererSize() {
  if (mobile.value || !hasRenderableArea()) return
  if (!renderer || rendererDirty) {
    scheduleRenderer()
    return
  }
  renderer.resize()
  renderer.refresh()
}

function updateSelection() {
  if (!sigmaGraph || !renderer) return
  applyPresentation()
  if (props.selectedNodeId && sigmaGraph.hasNode(props.selectedNodeId)) {
    const position = renderer.getNodeDisplayData(props.selectedNodeId)
    if (position) renderer.getCamera().animate(
      { x: position.x, y: position.y, ratio: 0.42 },
      { duration: 320 },
    )
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
  if (next !== mobile.value) {
    mobile.value = next
    if (next) {
      renderRequest += 1
      rendererDirty = false
      destroyRenderer()
      return
    }
    requestRenderer()
    return
  }
  updateRendererSize()
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
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(updateRendererSize)
    if (surface.value) resizeObserver.observe(surface.value)
  }
  themeObserver = new MutationObserver(() => {
    themeRevision.value += 1
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  requestRenderer()
})
onBeforeUnmount(() => {
  renderRequest += 1
  window.removeEventListener('resize', updateViewport)
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  destroyRenderer()
})
</script>

<template>
  <div ref="surface" class="graph-surface">
    <div v-if="!mobile" ref="container" class="sigma-container" aria-label="本地知识图谱"></div>
    <div v-if="!mobile && selectedNode" class="graph-focus-summary" role="status">
      <i :style="{ background: nodeColor(selectedNode) }"></i>
      <span>
        <strong>{{ selectedNode.label }}</strong>
        <small>
          {{ selectedNode.kind }} · {{ focus.connectedNodeIds.size }} 条直接连接
          <template v-if="selectedCommunity && selectedCommunity.node_count > 1"> · {{ selectedCommunity.label }}</template>
        </small>
      </span>
      <button type="button" title="退出聚焦" aria-label="退出节点聚焦" @click="emit('select', null)">
        <X :size="15" />
      </button>
    </div>
    <div v-else-if="!mobile" class="graph-global-summary">
      {{ communities?.analysis.community_count ?? 0 }} 个社区 · {{ sourceCount }} 个来源文件
    </div>
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
.sigma-container { position:absolute; inset:0; }.graph-controls { position:absolute; right:16px; bottom:16px; display:flex; flex-direction:column; overflow:hidden; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:color-mix(in srgb,var(--sage-surface) 92%,transparent); box-shadow:var(--sage-shadow-sm); backdrop-filter:blur(10px); }.graph-controls button { display:grid; place-items:center; width:36px; height:36px; padding:0; border:0; border-bottom:1px solid var(--sage-border); color:var(--sage-text-secondary); background:transparent; }.graph-controls button:last-child { border-bottom:0; }.graph-controls button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.graph-error { position:absolute; top:16px; left:50%; margin:0; padding:8px 11px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-danger); background:var(--sage-surface); transform:translateX(-50%); }
.graph-focus-summary { position:absolute; left:16px; bottom:16px; display:grid; grid-template-columns:10px minmax(0,1fr) 28px; align-items:center; gap:10px; width:min(390px,calc(100% - 82px)); min-height:54px; padding:8px 8px 8px 12px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-lg); color:var(--sage-text); background:color-mix(in srgb,var(--sage-surface) 92%,transparent); box-shadow:var(--sage-shadow-sm); backdrop-filter:blur(12px); }.graph-focus-summary>i { width:9px; height:9px; border-radius:50%; box-shadow:0 0 0 4px color-mix(in srgb,currentColor 12%,transparent); }.graph-focus-summary span,.graph-focus-summary strong,.graph-focus-summary small { display:block; min-width:0; }.graph-focus-summary strong { overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.graph-focus-summary small { overflow:hidden; margin-top:3px; color:var(--sage-text-muted); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.graph-focus-summary button { display:grid; place-items:center; width:28px; height:28px; padding:0; border:0; border-radius:var(--sage-radius); color:var(--sage-text-muted); background:transparent; }.graph-focus-summary button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }
.graph-global-summary { position:absolute; left:16px; bottom:16px; padding:7px 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:color-mix(in srgb,var(--sage-surface) 90%,transparent); font-size:11px; backdrop-filter:blur(8px); }
.mobile-node-list { height:100%; overflow:auto; padding:8px 14px 24px; }.mobile-node-list button { display:grid; grid-template-columns:10px minmax(0,1fr); align-items:center; gap:11px; width:100%; min-height:58px; padding:8px 4px; border:0; border-bottom:1px solid var(--sage-border); text-align:left; color:var(--sage-text); background:transparent; }.mobile-node-list button.active { background:var(--sage-source-bg); }.mobile-node-list i { width:9px; height:9px; border-radius:50%; }.mobile-node-list span,.mobile-node-list strong,.mobile-node-list small { display:block; min-width:0; }.mobile-node-list strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:var(--sage-font-md); }.mobile-node-list small { margin-top:3px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.mobile-node-list>p { color:var(--sage-text-muted); text-align:center; }
@media (prefers-reduced-motion:reduce) { .graph-controls button { transition:none; } }
</style>
