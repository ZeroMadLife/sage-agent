<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Focus, Minus, Plus, X } from 'lucide-vue-next'
import Graph from 'graphology'
import type Sigma from 'sigma'
import type { NodeHoverDrawingFunction } from 'sigma/rendering'
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
  graphLegendItems,
  graphPerformanceProfile,
  graphScopeNodeIds,
  selectedNodePresentation,
  shortestGraphPath,
  type KnowledgeGraphScopeMode,
} from './knowledgeGraphPresentation'

const props = withDefaults(defineProps<{
  graph: KnowledgeGraph
  communities: KnowledgeGraphCommunities | null
  selectedNodeId: string | null
  colorMode: 'type' | 'community'
  visibleKinds: KnowledgeGraphNodeKind[]
  query: string
  scopeMode?: KnowledgeGraphScopeMode
  depth?: 1 | 2 | 3
  goalNodeIds?: string[]
  showGoalPath?: boolean
}>(), {
  scopeMode: 'global',
  depth: 2,
  goalNodeIds: () => [],
  showGoalPath: false,
})

const emit = defineEmits<{
  select: [nodeId: string | null]
}>()

const surface = ref<HTMLElement | null>(null)
const container = ref<HTMLElement | null>(null)
const compactViewport = ref(window.innerWidth < 680)
const rendererError = ref('')
const themeRevision = ref(0)
let renderer: Sigma | null = null
let sigmaGraph: Graph | null = null
let selectionContext: CanvasRenderingContext2D | null = null
let themeObserver: MutationObserver | null = null
let resizeObserver: ResizeObserver | null = null
let layoutSupervisor: { start(): void; stop(): void; kill(): void } | null = null
let layoutStopTimer: ReturnType<typeof setTimeout> | null = null
let renderRequest = 0
let rendererPending = false
let rendererDirty = true
let hoveredNodeId: string | null = null
let dimNodeColor = 'rgba(126, 132, 142, 0.2)'
let dimEdgeColor = 'rgba(126, 132, 142, 0.08)'
let focusEdgeColor = '#58c78b'
let goalPathEdgeColor = '#b46b27'
let hoverSurfaceColor = '#25282d'
let hoverBorderColor = '#4a5059'
let hoverTextColor = '#f3f4f6'

const drawSageNodeHover: NodeHoverDrawingFunction = (context, data, settings) => {
  const label = typeof data.label === 'string' ? data.label : ''
  const fontSize = settings.labelSize
  const paddingX = 8
  const boxHeight = fontSize + 10
  const labelX = data.x + data.size + 7

  context.save()
  context.font = `${settings.labelWeight} ${fontSize}px ${settings.labelFont}`
  context.lineWidth = 1
  context.shadowBlur = 12
  context.shadowColor = 'rgba(0, 0, 0, 0.28)'

  context.beginPath()
  context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2)
  context.lineWidth = 4
  context.strokeStyle = hoverSurfaceColor
  context.stroke()
  context.beginPath()
  context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2)
  context.lineWidth = 1.5
  context.strokeStyle = data.color
  context.stroke()

  if (label) {
    const boxWidth = Math.ceil(context.measureText(label).width) + paddingX * 2
    const boxY = data.y - boxHeight / 2
    context.beginPath()
    context.roundRect(labelX, boxY, boxWidth, boxHeight, 5)
    context.fillStyle = hoverSurfaceColor
    context.fill()
    context.shadowBlur = 0
    context.strokeStyle = hoverBorderColor
    context.stroke()
    context.fillStyle = hoverTextColor
    context.fillText(label, labelX + paddingX, data.y + fontSize / 3)
  }
  context.restore()
}

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
const goalPath = computed(() => props.showGoalPath
  ? shortestGraphPath(props.graph, props.selectedNodeId, props.goalNodeIds)
  : null)
const scopeNodeIds = computed(() => {
  const ids = graphScopeNodeIds(props.graph, {
    mode: props.scopeMode,
    depth: props.depth,
    anchorNodeId: props.selectedNodeId,
    goalNodeIds: props.goalNodeIds,
  })
  for (const nodeId of goalPath.value?.nodeIds ?? []) ids.add(nodeId)
  return ids
})
const featuredLabels = computed(() => featuredNodeIds(props.graph, props.communities, 1))
const focus = computed(() => graphFocus(props.graph, props.selectedNodeId, 2))
const selectedNode = computed(() => props.graph.nodes.find(
  (node) => node.node_id === props.selectedNodeId,
) ?? null)
const selectedNodeVisible = computed(() => Boolean(selectedNode.value && isNodeVisible(selectedNode.value)))
const selectedMetric = computed(() => (
  props.selectedNodeId ? metricByNode.value.get(props.selectedNodeId) : null
))
const selectedCommunity = computed(() => props.communities?.communities.find(
  (item) => item.community_id === selectedMetric.value?.community_id,
) ?? null)
const scopeLabel = computed(() => ({ global: '全局图谱', goal: '目标图谱', local: '局部图谱' }[props.scopeMode]))
const sourceCount = computed(() => props.graph.nodes.filter(
  (node) => node.kind === 'source' && isNodeVisible(node),
).length)
const visibleNodeCount = computed(() => props.graph.nodes.filter(isNodeVisible).length)
const performanceProfile = computed(() => graphPerformanceProfile(
  props.graph.nodes.length,
  props.graph.edges.length,
  compactViewport.value,
))
const listFallback = computed(() => performanceProfile.value.useListFallback || Boolean(rendererError.value))
const legendItems = computed(() => graphLegendItems(
  props.graph,
  props.communities,
  props.colorMode,
  { type: typeColors, community: communityColor.value },
))
const mobileNodes = computed(() => props.graph.nodes
  .filter(isNodeVisible)
  .sort((left, right) => {
    const degree = (degreeByNode.value.get(right.node_id) ?? 0)
      - (degreeByNode.value.get(left.node_id) ?? 0)
    return degree || left.label.localeCompare(right.label, 'zh-CN')
  })
  .slice(0, 80))

function isNodeVisible(node: KnowledgeGraphNode) {
  if (!scopeNodeIds.value.has(node.node_id)) return false
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
  return `sage.knowledge.graph.layout.v7.${props.graph.snapshot.graph_revision}.${analysisRevision}`
}

function canvasLabel(label: string) {
  const normalized = label.replace(/\s+/g, ' ').trim()
  return normalized.length > 20 ? `${normalized.slice(0, 19)}…` : normalized
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
  const positions: Record<string, { x: number; y: number }> = Object.fromEntries(cachedPositions())
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
  const focusNodeId = props.selectedNodeId ?? hoveredNodeId
  const focusValue = focusNodeId === props.selectedNodeId
    ? focus.value
    : graphFocus(props.graph, focusNodeId, 2)

  sigmaGraph.forEachNode((nodeId, attributes) => {
    const selected = nodeId === props.selectedNodeId
    const focused = nodeId === focusNodeId
    const connected = focusValue.connectedNodeIds.has(nodeId)
    const hovered = nodeId === hoveredNodeId
    const onGoalPath = Boolean(goalPath.value?.nodeIds.includes(nodeId))
    const baseColor = String(attributes.baseColor)
    const baseSize = Number(attributes.baseSize)
    const selectedStyle = selected ? selectedNodePresentation(baseColor, baseSize) : null
    sigmaGraph?.setNodeAttribute(
      nodeId,
      'color',
      selectedStyle?.color ?? (focusNodeId && !focused && !connected && !onGoalPath ? dimNodeColor : baseColor),
    )
    sigmaGraph?.setNodeAttribute(
      nodeId,
      'size',
      selectedStyle?.size ?? (focused ? baseSize * 1.22 : onGoalPath ? baseSize * 1.14 : connected || hovered ? baseSize * 1.08 : baseSize),
    )
    sigmaGraph?.setNodeAttribute(
      nodeId,
      'forceLabel',
      hovered || onGoalPath || (focusNodeId
        ? focusValue.labelNodeIds.has(nodeId)
        : featuredLabels.value.has(nodeId)),
    )
    sigmaGraph?.setNodeAttribute(nodeId, 'highlighted', selectedStyle?.highlighted ?? false)
    sigmaGraph?.setNodeAttribute(nodeId, 'zIndex', selectedStyle?.zIndex ?? (focused ? 5 : onGoalPath ? 4 : connected || hovered ? 3 : 1))
  })

  sigmaGraph.forEachEdge((edgeId, attributes) => {
    const focused = focusValue.focusedEdgeIds.has(edgeId)
    const onGoalPath = Boolean(goalPath.value?.edgeIds.includes(edgeId))
    sigmaGraph?.setEdgeAttribute(
      edgeId,
      'hidden',
      Boolean(attributes.globalHidden && !focused && !onGoalPath),
    )
    sigmaGraph?.setEdgeAttribute(
      edgeId,
      'color',
      onGoalPath
        ? goalPathEdgeColor
        : focusNodeId ? (focused ? focusEdgeColor : dimEdgeColor) : String(attributes.baseColor),
    )
    sigmaGraph?.setEdgeAttribute(
      edgeId,
      'size',
      onGoalPath
        ? Math.max(1.7, Number(attributes.baseSize) * 1.7)
        : focusNodeId && focused
        ? Math.max(1.35, Number(attributes.baseSize) * 1.45)
        : focusNodeId
          ? Math.min(0.34, Number(attributes.baseSize))
        : Number(attributes.baseSize),
    )
    sigmaGraph?.setEdgeAttribute(edgeId, 'zIndex', onGoalPath ? 3 : focusNodeId && focused ? 2 : 0)
  })
  renderer?.refresh()
}

function drawSelectionHalo() {
  if (!renderer || !selectionContext) return
  const { width, height } = renderer.getDimensions()
  selectionContext.clearRect(0, 0, width, height)
  if (!props.selectedNodeId) return
  const data = renderer.getNodeDisplayData(props.selectedNodeId)
  if (!data || data.hidden) return
  const position = renderer.framedGraphToViewport(data)
  const size = renderer.scaleSize(data.size)

  selectionContext.save()
  selectionContext.beginPath()
  selectionContext.arc(position.x, position.y, size + 3.5, 0, Math.PI * 2)
  selectionContext.lineWidth = 3
  selectionContext.strokeStyle = hoverSurfaceColor
  selectionContext.stroke()
  selectionContext.beginPath()
  selectionContext.arc(position.x, position.y, size + 5.5, 0, Math.PI * 2)
  selectionContext.lineWidth = 1.75
  selectionContext.strokeStyle = data.color
  selectionContext.stroke()
  selectionContext.restore()
}

function hasRenderableArea() {
  if (!container.value) return false
  const bounds = container.value.getBoundingClientRect()
  return bounds.width >= 2 && bounds.height >= 2
}

function destroyRenderer() {
  if (layoutStopTimer) clearTimeout(layoutStopTimer)
  layoutStopTimer = null
  layoutSupervisor?.kill()
  layoutSupervisor = null
  renderer?.kill()
  renderer = null
  sigmaGraph = null
  selectionContext = null
  hoveredNodeId = null
}

async function mountRenderer(requestId: number) {
  rendererError.value = ''
  if (performanceProfile.value.useListFallback || visibleNodeCount.value === 0 || !container.value) {
    if (performanceProfile.value.useListFallback) destroyRenderer()
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
  const reviewColor = rootStyles.getPropertyValue('--sage-review-strong').trim() || '#b46b27'
  const darkTheme = document.documentElement.dataset.theme === 'dark'
  const smallGraph = performanceProfile.value.tier === 'small'
  const mutedEdgeColor = darkTheme
    ? `rgba(139, 148, 163, ${smallGraph ? 0.18 : 0.08})`
    : `rgba(113, 122, 137, ${smallGraph ? 0.2 : 0.1})`
  dimNodeColor = darkTheme ? 'rgba(139, 148, 163, 0.09)' : 'rgba(113, 122, 137, 0.12)'
  dimEdgeColor = darkTheme ? 'rgba(139, 148, 163, 0.08)' : 'rgba(113, 122, 137, 0.08)'
  focusEdgeColor = brandColor
  goalPathEdgeColor = reviewColor
  hoverSurfaceColor = rootStyles.getPropertyValue('--sage-surface-raised').trim()
    || (darkTheme ? '#25282d' : '#ffffff')
  hoverBorderColor = rootStyles.getPropertyValue('--sage-border-strong').trim()
    || (darkTheme ? '#4a5059' : '#c7ccd4')
  hoverTextColor = labelColor
  let missingCachedPosition = false
  const visibleNodes = props.graph.nodes.filter(isNodeVisible)
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.node_id))
  visibleNodes.forEach((node, index) => {
    const cachedPosition = positions.get(node.node_id)
    if (!cachedPosition) missingCachedPosition = true
    const position = cachedPosition
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
      label: canvasLabel(node.label),
      color: baseColor,
      baseColor,
      size: baseSize,
      baseSize,
      hidden: false,
      forceLabel: false,
      zIndex: 1,
    })
  })
  const preferredNodeId = props.selectedNodeId
  const renderedEdges = props.graph.edges
    .filter((edge) => (
      visibleNodeIds.has(edge.source_node_id) && visibleNodeIds.has(edge.target_node_id)
    ))
    .slice()
    .sort((left, right) => {
      const leftPath = Number(goalPath.value?.edgeIds.includes(left.edge_id))
      const rightPath = Number(goalPath.value?.edgeIds.includes(right.edge_id))
      if (leftPath !== rightPath) return rightPath - leftPath
      const leftFocused = Number(
        left.source_node_id === preferredNodeId || left.target_node_id === preferredNodeId,
      )
      const rightFocused = Number(
        right.source_node_id === preferredNodeId || right.target_node_id === preferredNodeId,
      )
      if (leftFocused !== rightFocused) return rightFocused - leftFocused
      if (left.kind !== right.kind) return left.kind === 'WIKILINK' ? -1 : 1
      return (right.weight * right.confidence) - (left.weight * left.confidence)
    })
    .slice(0, performanceProfile.value.maxRenderedEdges)
  for (const edge of renderedEdges) {
    if (!graph.hasNode(edge.source_node_id) || !graph.hasNode(edge.target_node_id)) continue
    const evidenceStrength = Math.min(1, Math.max(0.35, edge.weight * edge.confidence))
    const baseColor = edge.kind === 'WIKILINK'
      ? (darkTheme
        ? `rgba(111, 148, 205, ${Math.min(0.3, (smallGraph ? 0.2 : 0.12) * (0.55 + evidenceStrength * 0.65))})`
        : `rgba(72, 114, 177, ${Math.min(0.34, (smallGraph ? 0.24 : 0.15) * (0.55 + evidenceStrength * 0.65))})`)
      : mutedEdgeColor
    const edgeScale = smallGraph ? 1.8 : performanceProfile.value.tier === 'medium' ? 1.25 : 1
    const baseSize = (edge.kind === 'WIKILINK'
      ? Math.min(0.72, 0.34 + edge.weight * 0.08)
      : Math.min(0.5, 0.24 + edge.confidence * 0.12)) * edgeScale
    const globalHidden = edge.kind === 'EVIDENCED_BY'
    graph.addEdgeWithKey(edge.edge_id, edge.source_node_id, edge.target_node_id, {
      color: baseColor,
      baseColor,
      size: baseSize,
      baseSize,
      hidden: globalHidden,
      globalHidden,
      weight: edge.kind === 'WIKILINK' ? Math.max(0.5, edge.weight) : 0.06,
      type: 'line',
      zIndex: 0,
    })
  }
  try {
    renderer = new SigmaRenderer(graph, container.value, {
      allowInvalidContainer: true,
      defaultDrawNodeHover: drawSageNodeHover,
      defaultEdgeColor: mutedEdgeColor,
      defaultEdgeType: 'line',
      hideEdgesOnMove: performanceProfile.value.hideEdgesOnMove,
      hideLabelsOnMove: true,
      inertiaDuration: 320,
      inertiaRatio: 1.8,
      labelColor: { color: labelColor },
      labelDensity: performanceProfile.value.labelDensity,
      labelFont,
      labelGridCellSize: performanceProfile.value.tier === 'small' ? 120 : 160,
      labelRenderedSizeThreshold: performanceProfile.value.labelRenderedSizeThreshold,
      labelSize: 12,
      labelWeight: '500',
      maxCameraRatio: 4,
      minCameraRatio: 0.16,
      renderEdgeLabels: false,
      stagePadding: compactViewport.value ? 48 : 64,
      zoomDuration: 260,
      zIndex: true,
    })
    renderer.createCanvasContext('selection', {
      style: { pointerEvents: 'none' },
    })
    selectionContext = container.value.querySelector<HTMLCanvasElement>('.sigma-selection')
      ?.getContext('2d') ?? null
    sigmaGraph = graph
    hoveredNodeId = null
    renderer.on('afterRender', drawSelectionHalo)
    applyPresentation()
    renderer.on('clickNode', ({ node }) => emit('select', node))
    renderer.on('clickStage', () => emit('select', null))
    renderer.on('enterNode', ({ node }) => {
      hoveredNodeId = node
      if (container.value) container.value.style.cursor = 'pointer'
      applyPresentation()
    })
    renderer.on('leaveNode', () => {
      hoveredNodeId = null
      if (container.value) container.value.style.cursor = 'default'
      applyPresentation()
    })
    if (missingCachedPosition && graph.order > 1 && graph.size > 0) {
      const { default: FA2LayoutSupervisor } = await import('graphology-layout-forceatlas2/worker')
      if (requestId !== renderRequest || sigmaGraph !== graph) return
      layoutSupervisor = new FA2LayoutSupervisor(graph, {
        settings: {
          edgeWeightInfluence: 0.55,
          gravity: 0.08,
          linLogMode: true,
          scalingRatio: performanceProfile.value.tier === 'large' ? 48 : 36,
          strongGravityMode: false,
          slowDown: performanceProfile.value.tier === 'small' ? 14 : 20,
          barnesHutOptimize: performanceProfile.value.barnesHutOptimize,
          barnesHutTheta: 0.7,
        },
      })
      layoutSupervisor.start()
      layoutStopTimer = setTimeout(() => {
        layoutSupervisor?.stop()
        layoutSupervisor?.kill()
        layoutSupervisor = null
        layoutStopTimer = null
        savePositions(graph)
        renderer?.refresh()
      }, performanceProfile.value.layoutDurationMs)
    }
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
  if (listFallback.value) return
  if (!hasRenderableArea()) {
    layoutSupervisor?.stop()
    return
  }
  if (layoutSupervisor && layoutStopTimer) layoutSupervisor.start()
  if (!renderer || rendererDirty) {
    scheduleRenderer()
    return
  }
  renderer.resize()
  renderer.refresh()
}

function updateSelection() {
  if (performanceProfile.value.tier === 'large') {
    requestRenderer()
    return
  }
  if (!sigmaGraph || !renderer) return
  applyPresentation()
  const camera = renderer.getCamera()
  if (props.selectedNodeId && sigmaGraph.hasNode(props.selectedNodeId)) {
    const position = renderer.getNodeDisplayData(props.selectedNodeId)
    if (position) camera.animate(
      { x: position.x, y: position.y, ratio: Math.min(camera.getState().ratio, 0.72) },
      { duration: 460, easing: 'quadraticInOut' },
    )
  } else {
    camera.animatedReset({ duration: 360, easing: 'quadraticInOut' })
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
  if (next !== compactViewport.value) {
    compactViewport.value = next
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
    props.scopeMode,
    props.depth,
    props.goalNodeIds.join(','),
    props.showGoalPath,
    props.communities?.analysis.analysis_revision,
    compactViewport.value,
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
  <div ref="surface" class="graph-surface" :data-scope="scopeMode">
    <div v-if="!listFallback && visibleNodeCount" ref="container" class="sigma-container" aria-label="本地知识图谱"></div>
    <div v-if="!listFallback && selectedNode && selectedNodeVisible" class="graph-focus-summary" role="status">
      <i :style="{ background: nodeColor(selectedNode) }"></i>
      <span>
        <strong>{{ selectedNode.label }}</strong>
        <small>
          {{ selectedNode.kind }} · {{ focus.connectedNodeIds.size }} 条直接连接
          <template v-if="selectedCommunity && selectedCommunity.node_count > 1"> · {{ selectedCommunity.label }}</template>
          <template v-if="goalPath"> · 到目标 {{ goalPath.edgeIds.length }} 跳</template>
        </small>
      </span>
      <button type="button" title="退出聚焦" aria-label="退出节点聚焦" @click="emit('select', null)">
        <X :size="15" />
      </button>
    </div>
    <div v-else-if="!listFallback" class="graph-global-summary">
      <strong>{{ scopeLabel }}</strong>
      <span>
        {{ visibleNodeCount }} 个可见节点
        <template v-if="visibleKinds.includes('source')"> · {{ sourceCount }} 个来源</template>
        <template v-else> · 来源已隐藏</template>
        <template v-if="scopeMode !== 'global'"> · 深度 {{ depth }}</template>
      </span>
    </div>
    <div v-if="!listFallback && !visibleNodeCount" class="graph-scope-empty" role="status">
      <strong>{{ scopeLabel }}暂无匹配节点</strong>
      <span>{{ scopeMode === 'goal' ? '当前学习目标还没有匹配到可见节点。' : '调整节点类型、搜索条件或探索范围。' }}</span>
    </div>
    <div v-if="!listFallback" class="graph-legend-panel" :data-mode="colorMode" aria-label="图谱图例">
      <span v-for="item in legendItems" :key="item.id" :title="`${item.label} · ${item.count}`">
        <i :style="{ background: item.color }"></i>
        <b>{{ item.label }}</b>
        <small>{{ item.count }}</small>
      </span>
    </div>
    <div v-if="!listFallback" class="graph-controls" aria-label="图谱缩放">
      <button type="button" title="放大" aria-label="放大图谱" @click="zoom('in')"><Plus :size="17" /></button>
      <button type="button" title="缩小" aria-label="缩小图谱" @click="zoom('out')"><Minus :size="17" /></button>
      <button type="button" title="居中" aria-label="重置图谱视图" @click="resetCamera"><Focus :size="17" /></button>
    </div>
    <p v-if="rendererError" class="graph-error" role="status">图谱渲染已降级：{{ rendererError }}</p>
    <div v-if="listFallback" class="mobile-node-list" aria-label="知识节点列表">
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
.sigma-container { position:absolute; inset:0; cursor:grab; }.sigma-container:active { cursor:grabbing; }.graph-controls { position:absolute; right:16px; bottom:16px; display:flex; flex-direction:column; overflow:hidden; border:1px solid var(--sage-border); border-radius:var(--sage-radius); background:color-mix(in srgb,var(--sage-surface) 92%,transparent); box-shadow:var(--sage-shadow-sm); backdrop-filter:blur(10px); }.graph-controls button { display:grid; place-items:center; width:36px; height:36px; padding:0; border:0; border-bottom:1px solid var(--sage-border); color:var(--sage-text-secondary); background:transparent; }.graph-controls button:last-child { border-bottom:0; }.graph-controls button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }.graph-error { position:absolute; top:16px; left:50%; margin:0; padding:8px 11px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-danger); background:var(--sage-surface); transform:translateX(-50%); }
.graph-focus-summary { position:absolute; left:16px; bottom:16px; display:grid; grid-template-columns:10px minmax(0,1fr) 28px; align-items:center; gap:10px; width:min(390px,calc(100% - 82px)); min-height:54px; padding:8px 8px 8px 12px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-lg); color:var(--sage-text); background:color-mix(in srgb,var(--sage-surface) 92%,transparent); box-shadow:var(--sage-shadow-sm); backdrop-filter:blur(12px); }.graph-focus-summary>i { width:9px; height:9px; border-radius:50%; box-shadow:0 0 0 4px color-mix(in srgb,currentColor 12%,transparent); }.graph-focus-summary span,.graph-focus-summary strong,.graph-focus-summary small { display:block; min-width:0; }.graph-focus-summary strong { overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.graph-focus-summary small { overflow:hidden; margin-top:3px; color:var(--sage-text-muted); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.graph-focus-summary button { display:grid; place-items:center; width:28px; height:28px; padding:0; border:0; border-radius:var(--sage-radius); color:var(--sage-text-muted); background:transparent; }.graph-focus-summary button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }
.graph-global-summary { position:absolute; left:16px; bottom:16px; display:flex; align-items:center; gap:7px; padding:7px 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:color-mix(in srgb,var(--sage-surface) 90%,transparent); font-size:11px; backdrop-filter:blur(8px); }.graph-global-summary strong { color:var(--sage-text-secondary); font-size:11px; }
.graph-scope-empty { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; flex-direction:column; gap:5px; padding:24px; color:var(--sage-text-muted); text-align:center; }.graph-scope-empty strong { color:var(--sage-text-secondary); font-size:var(--sage-font-md); }.graph-scope-empty span { max-width:320px; font-size:var(--sage-font-xs); line-height:1.55; }
.graph-legend-panel { position:absolute; top:14px; left:14px; display:flex; flex-wrap:wrap; gap:5px; width:min(520px,calc(100% - 100px)); pointer-events:none; }.graph-legend-panel span { display:grid; grid-template-columns:8px minmax(0,auto) auto; align-items:center; gap:5px; min-width:0; height:26px; padding:0 7px; border:1px solid color-mix(in srgb,var(--sage-border) 76%,transparent); border-radius:var(--sage-radius-sm); color:var(--sage-text-secondary); background:color-mix(in srgb,var(--sage-surface) 84%,transparent); font-size:10px; backdrop-filter:blur(8px); }.graph-legend-panel i { width:7px; height:7px; border-radius:50%; }.graph-legend-panel b { max-width:105px; overflow:hidden; font-weight:600; text-overflow:ellipsis; white-space:nowrap; }.graph-legend-panel small { color:var(--sage-text-muted); font-size:9px; }
.mobile-node-list { height:100%; overflow:auto; padding:8px 14px 24px; }.mobile-node-list button { display:grid; grid-template-columns:10px minmax(0,1fr); align-items:center; gap:11px; width:100%; min-height:58px; padding:8px 4px; border:0; border-bottom:1px solid var(--sage-border); text-align:left; color:var(--sage-text); background:transparent; }.mobile-node-list button.active { background:var(--sage-source-bg); }.mobile-node-list i { width:9px; height:9px; border-radius:50%; }.mobile-node-list span,.mobile-node-list strong,.mobile-node-list small { display:block; min-width:0; }.mobile-node-list strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:var(--sage-font-md); }.mobile-node-list small { margin-top:3px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.mobile-node-list>p { color:var(--sage-text-muted); text-align:center; }
@media (max-width:679px) {
  .graph-legend-panel,.graph-global-summary { display:none; }
  .graph-controls { right:10px; bottom:82px; }
  .graph-focus-summary { left:10px; bottom:10px; width:calc(100% - 20px); }
}
@media (prefers-reduced-motion:reduce) { .graph-controls button { transition:none; } }
</style>
