import type {
  KnowledgeGraph,
  KnowledgeGraphCommunities,
  KnowledgeGraphNode,
} from '../../types/api'

export type KnowledgeGraphFocus = {
  connectedNodeIds: Set<string>
  focusedEdgeIds: Set<string>
  labelNodeIds: Set<string>
}

export type KnowledgeGraphLegendItem = {
  id: string
  label: string
  count: number
  color: string
}

export type KnowledgeGraphPerformanceProfile = {
  tier: 'small' | 'medium' | 'large' | 'fallback'
  layoutDurationMs: number
  maxRenderedEdges: number
  labelDensity: number
  labelRenderedSizeThreshold: number
  hideEdgesOnMove: boolean
  barnesHutOptimize: boolean
  useListFallback: boolean
}

export type KnowledgeGraphEdgePresentation = {
  color: string
  size: number
  hidden: false
}

export type KnowledgeGraphInteractionPalette = {
  dimNodeColor: string
  dimEdgeColor: string
  focusEdgeStrength: number
}

export type KnowledgeGraphScopeMode = 'global' | 'goal' | 'local'

export type KnowledgeGraphPath = {
  nodeIds: string[]
  edgeIds: string[]
}

export function graphScopeNodeIds(
  graph: KnowledgeGraph,
  options: {
    mode: KnowledgeGraphScopeMode
    depth: number
    anchorNodeId: string | null
    goalNodeIds: readonly string[]
  },
) {
  if (options.mode === 'global') return new Set(graph.nodes.map((node) => node.node_id))
  const seeds = options.mode === 'local'
    ? options.anchorNodeId ? [options.anchorNodeId] : []
    : options.goalNodeIds
  return expandNodeNeighborhood(graph, seeds, options.depth)
}

export function shortestGraphPath(
  graph: KnowledgeGraph,
  startNodeId: string | null,
  goalNodeIds: readonly string[],
): KnowledgeGraphPath | null {
  if (!startNodeId) return null
  const goals = new Set(goalNodeIds)
  if (!goals.size) return null
  if (goals.has(startNodeId)) return { nodeIds: [startNodeId], edgeIds: [] }

  const adjacency = graphAdjacency(graph)
  const queue = [startNodeId]
  const visited = new Set(queue)
  const previous = new Map<string, { nodeId: string; edgeId: string }>()
  let target = ''
  while (queue.length && !target) {
    const current = queue.shift() as string
    for (const relation of adjacency.get(current) ?? []) {
      if (visited.has(relation.nodeId)) continue
      visited.add(relation.nodeId)
      previous.set(relation.nodeId, { nodeId: current, edgeId: relation.edgeId })
      if (goals.has(relation.nodeId)) {
        target = relation.nodeId
        break
      }
      queue.push(relation.nodeId)
    }
  }
  if (!target) return null

  const nodeIds = [target]
  const edgeIds: string[] = []
  while (nodeIds[0] !== startNodeId) {
    const parent = previous.get(nodeIds[0])
    if (!parent) return null
    nodeIds.unshift(parent.nodeId)
    edgeIds.unshift(parent.edgeId)
  }
  return { nodeIds, edgeIds }
}

function expandNodeNeighborhood(graph: KnowledgeGraph, seeds: readonly string[], depth: number) {
  const available = new Set(graph.nodes.map((node) => node.node_id))
  const frontier = seeds.filter((nodeId) => available.has(nodeId))
  const visible = new Set(frontier)
  const adjacency = graphAdjacency(graph)
  let current = frontier
  for (let level = 0; level < Math.max(0, Math.floor(depth)); level += 1) {
    const next: string[] = []
    for (const nodeId of current) {
      for (const relation of adjacency.get(nodeId) ?? []) {
        if (visible.has(relation.nodeId)) continue
        visible.add(relation.nodeId)
        next.push(relation.nodeId)
      }
    }
    current = next
    if (!current.length) break
  }
  return visible
}

function graphAdjacency(graph: KnowledgeGraph) {
  const adjacency = new Map<string, Array<{ nodeId: string; edgeId: string }>>()
  for (const edge of graph.edges) {
    const source = adjacency.get(edge.source_node_id) ?? []
    source.push({ nodeId: edge.target_node_id, edgeId: edge.edge_id })
    adjacency.set(edge.source_node_id, source)
    const target = adjacency.get(edge.target_node_id) ?? []
    target.push({ nodeId: edge.source_node_id, edgeId: edge.edge_id })
    adjacency.set(edge.target_node_id, target)
  }
  return adjacency
}

export function graphPerformanceProfile(
  nodeCount: number,
  edgeCount: number,
  compactViewport = false,
): KnowledgeGraphPerformanceProfile {
  if (nodeCount > 5_000 || (compactViewport && nodeCount > 1_200)) {
    return {
      tier: 'fallback', layoutDurationMs: 0, maxRenderedEdges: 0,
      labelDensity: 0, labelRenderedSizeThreshold: 100,
      hideEdgesOnMove: true, barnesHutOptimize: true, useListFallback: true,
    }
  }
  if (nodeCount <= 200) {
    return {
      tier: 'small', layoutDurationMs: 1_600, maxRenderedEdges: edgeCount,
      labelDensity: 0.075, labelRenderedSizeThreshold: 8,
      hideEdgesOnMove: false, barnesHutOptimize: nodeCount > 100, useListFallback: false,
    }
  }
  if (nodeCount <= 1_000) {
    return {
      tier: 'medium', layoutDurationMs: 1_200, maxRenderedEdges: edgeCount,
      labelDensity: 0.04, labelRenderedSizeThreshold: 10,
      hideEdgesOnMove: edgeCount > 3_000, barnesHutOptimize: true, useListFallback: false,
    }
  }
  return {
    tier: 'large', layoutDurationMs: 800, maxRenderedEdges: Math.min(edgeCount, 20_000),
    labelDensity: 0.016, labelRenderedSizeThreshold: 12,
    hideEdgesOnMove: true, barnesHutOptimize: true, useListFallback: false,
  }
}

function mixedHex(
  foreground: readonly [number, number, number],
  background: readonly [number, number, number],
  strength: number,
) {
  const channels = foreground.map((value, index) => (
    Math.round(background[index] + (value - background[index]) * strength)
  ))
  return `#${channels.map((value) => value.toString(16).padStart(2, '0')).join('')}`
}

export function graphInteractionPalette(darkTheme: boolean): KnowledgeGraphInteractionPalette {
  return darkTheme
    ? {
        dimNodeColor: '#4a4f58',
        dimEdgeColor: '#292c31',
        focusEdgeStrength: 0.88,
      }
    : {
        dimNodeColor: '#d8dadd',
        dimEdgeColor: '#eef0f2',
        focusEdgeStrength: 0.84,
      }
}

export function graphEdgePresentation(
  kind: KnowledgeGraph['edges'][number]['kind'],
  weight: number,
  confidence: number,
  darkTheme: boolean,
  tier: KnowledgeGraphPerformanceProfile['tier'],
): KnowledgeGraphEdgePresentation {
  const strength = Math.min(1, Math.max(0.35, weight * confidence))
  const edgeScale = tier === 'small' ? 1.6 : tier === 'medium' ? 1.2 : 1

  if (kind === 'WIKILINK') {
    const strengthBase = darkTheme
      ? tier === 'small' ? 0.24 : tier === 'medium' ? 0.18 : 0.13
      : tier === 'small' ? 0.24 : tier === 'medium' ? 0.18 : 0.12
    const strengthRange = darkTheme
      ? tier === 'large' ? 0.04 : tier === 'medium' ? 0.05 : 0.06
      : tier === 'large' ? 0.06 : tier === 'medium' ? 0.08 : 0.1
    return {
      color: mixedHex(
        darkTheme ? [159, 168, 181] : [92, 99, 110],
        darkTheme ? [32, 33, 36] : [255, 255, 255],
        strengthBase + strength * strengthRange,
      ),
      size: Math.min(0.78, 0.38 + weight * 0.1) * edgeScale,
      hidden: false,
    }
  }

  const strengthBase = darkTheme
    ? tier === 'small' ? 0.12 : tier === 'medium' ? 0.08 : 0.055
    : tier === 'small' ? 0.19 : tier === 'medium' ? 0.13 : 0.08
  const strengthRange = darkTheme ? 0.07 : 0.09
  return {
    color: mixedHex(
      darkTheme ? [145, 154, 168] : [116, 123, 134],
      darkTheme ? [32, 33, 36] : [255, 255, 255],
      strengthBase + strength * strengthRange,
    ),
    size: Math.min(0.56, 0.3 + confidence * 0.14) * edgeScale,
    hidden: false,
  }
}

export function focusedGraphEdgeColor(baseNodeColor: string, darkTheme: boolean) {
  const match = /^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(baseNodeColor)
  if (!match) return baseNodeColor
  const [, red, green, blue] = match
  return mixedHex(
    [Number.parseInt(red, 16), Number.parseInt(green, 16), Number.parseInt(blue, 16)],
    darkTheme ? [32, 33, 36] : [255, 255, 255],
    graphInteractionPalette(darkTheme).focusEdgeStrength,
  )
}

function stableFraction(value: string) {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0) / 0xffffffff
}

function nodePriority(node: KnowledgeGraphNode, weightedDegree: number, bridgeScore: number) {
  const kindWeight = node.kind === 'source' ? 0 : 1
  return [kindWeight, weightedDegree, bridgeScore] as const
}

export function featuredNodeIds(
  graph: KnowledgeGraph,
  communities: KnowledgeGraphCommunities | null,
  limit = 2,
) {
  const metrics = new Map(communities?.node_metrics.map((item) => [item.node_id, item]) ?? [])
  const rankedNodes = graph.nodes
    .filter((node) => (metrics.get(node.node_id)?.degree ?? 0) > 0)
    .sort((left, right) => {
      const leftMetric = metrics.get(left.node_id)
      const rightMetric = metrics.get(right.node_id)
      const leftPriority = nodePriority(
        left,
        leftMetric?.weighted_degree ?? 0,
        leftMetric?.bridge_score ?? 0,
      )
      const rightPriority = nodePriority(
        right,
        rightMetric?.weighted_degree ?? 0,
        rightMetric?.bridge_score ?? 0,
      )
      for (let index = 0; index < leftPriority.length; index += 1) {
        if (leftPriority[index] !== rightPriority[index]) {
          return rightPriority[index] - leftPriority[index]
        }
      }
      return left.label.localeCompare(right.label, 'zh-CN')
    })
  if (!communities?.communities.length) {
    return new Set(rankedNodes.slice(0, limit).map((node) => node.node_id))
  }

  const featured: string[] = []
  const orderedCommunities = communities.communities
    .slice()
    .sort((left, right) => right.node_count - left.node_count)
  for (const community of orderedCommunities) {
    const representative = rankedNodes.find((node) => (
      metrics.get(node.node_id)?.community_id === community.community_id
      && !featured.includes(node.node_id)
    ))
    if (representative) featured.push(representative.node_id)
    if (featured.length >= limit) break
  }
  for (const node of rankedNodes) {
    if (!featured.includes(node.node_id)) featured.push(node.node_id)
    if (featured.length >= limit) break
  }
  return new Set(featured)
}

export function graphFocus(
  graph: KnowledgeGraph,
  selectedNodeId: string | null,
  maxNeighborLabels = 6,
): KnowledgeGraphFocus {
  if (!selectedNodeId) {
    return {
      connectedNodeIds: new Set(),
      focusedEdgeIds: new Set(),
      labelNodeIds: new Set(),
    }
  }

  const nodes = new Map(graph.nodes.map((node) => [node.node_id, node]))
  const connectedEdges = graph.edges
    .filter((edge) => (
      edge.source_node_id === selectedNodeId || edge.target_node_id === selectedNodeId
    ))
    .map((edge) => ({
      edge,
      neighborId: edge.source_node_id === selectedNodeId
        ? edge.target_node_id
        : edge.source_node_id,
    }))
    .sort((left, right) => {
      const leftNode = nodes.get(left.neighborId)
      const rightNode = nodes.get(right.neighborId)
      const kindDifference = Number(rightNode?.kind !== 'source') - Number(leftNode?.kind !== 'source')
      if (kindDifference) return kindDifference
      const scoreDifference = (right.edge.weight * right.edge.confidence)
        - (left.edge.weight * left.edge.confidence)
      if (scoreDifference) return scoreDifference
      return (leftNode?.label ?? '').localeCompare(rightNode?.label ?? '', 'zh-CN')
    })

  return {
    connectedNodeIds: new Set(connectedEdges.map((item) => item.neighborId)),
    focusedEdgeIds: new Set(connectedEdges.map((item) => item.edge.edge_id)),
    labelNodeIds: new Set([
      selectedNodeId,
      ...connectedEdges
        .filter((item) => nodes.get(item.neighborId)?.kind !== 'source')
        .slice(0, maxNeighborLabels)
        .map((item) => item.neighborId),
    ]),
  }
}

export function graphLegendItems(
  graph: KnowledgeGraph,
  communities: KnowledgeGraphCommunities | null,
  colorMode: 'type' | 'community',
  colors: { type: Record<string, string>; community: Map<string, string> },
  limit = 6,
): KnowledgeGraphLegendItem[] {
  if (colorMode === 'community' && communities?.communities.length) {
    return communities.communities
      .slice()
      .sort((left, right) => (
        right.node_count - left.node_count || left.label.localeCompare(right.label, 'zh-CN')
      ))
      .slice(0, limit)
      .map((community) => ({
        id: community.community_id,
        label: community.label.split(' / ')[0]?.trim() || community.label,
        count: community.node_count,
        color: colors.community.get(community.community_id) ?? '#8b5cf6',
      }))
  }

  const counts = new Map<string, number>()
  for (const node of graph.nodes) counts.set(node.kind, (counts.get(node.kind) ?? 0) + 1)
  const labels: Record<string, string> = {
    page: '页面', source: '来源', project: '项目', concept: '概念', decision: '决策', tool: '工具',
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, limit)
    .map(([kind, count]) => ({
      id: kind,
      label: labels[kind] ?? kind,
      count,
      color: colors.type[kind] ?? '#829356',
    }))
}

export function communitySeedPositions(
  graph: KnowledgeGraph,
  communities: KnowledgeGraphCommunities | null,
) {
  const communityByNode = new Map(
    communities?.node_metrics.map((item) => [item.node_id, item.community_id]) ?? [],
  )
  for (let pass = 0; pass < 2; pass += 1) {
    for (const edge of graph.edges) {
      const sourceCommunity = communityByNode.get(edge.source_node_id)
      const targetCommunity = communityByNode.get(edge.target_node_id)
      if (sourceCommunity && !targetCommunity) {
        communityByNode.set(edge.target_node_id, sourceCommunity)
      } else if (targetCommunity && !sourceCommunity) {
        communityByNode.set(edge.source_node_id, targetCommunity)
      }
    }
  }
  const groups = new Map<string, KnowledgeGraphNode[]>()
  for (const node of graph.nodes) {
    const communityId = communityByNode.get(node.node_id) ?? `unassigned:${node.node_id}`
    const group = groups.get(communityId) ?? []
    group.push(node)
    groups.set(communityId, group)
  }

  const orderedGroups = [...groups.entries()].sort((left, right) => (
    right[1].length - left[1].length || left[0].localeCompare(right[0])
  ))
  const positions = new Map<string, { x: number; y: number }>()
  const communityCenters: Array<{ x: number; y: number }> = [{ x: 0, y: 0 }]
  let communityCursor = 1
  let ring = 1
  while (communityCursor < orderedGroups.length) {
    const ringCount = Math.min(ring * 8, orderedGroups.length - communityCursor)
    const ringRadius = ring * 8
    const ringPhase = ring % 2 === 0 ? Math.PI / ringCount : 0
    for (let index = 0; index < ringCount; index += 1) {
      const angle = ringPhase + (Math.PI * 2 * index) / ringCount
      communityCenters.push({
        x: Math.cos(angle) * ringRadius,
        y: Math.sin(angle) * ringRadius,
      })
    }
    communityCursor += ringCount
    ring += 1
  }

  orderedGroups.forEach(([communityId, group], communityIndex) => {
    const center = communityCenters[communityIndex] ?? { x: 0, y: 0 }
    const localRadius = Math.max(1.6, 1.25 * Math.sqrt(group.length))
    const phase = stableFraction(communityId) * Math.PI * 2

    group
      .slice()
      .sort((left, right) => left.node_id.localeCompare(right.node_id))
      .forEach((node, nodeIndex) => {
        const angle = phase + (Math.PI * 2 * nodeIndex) / Math.max(group.length, 1)
        const radius = group.length === 1
          ? 0
          : localRadius * (0.72 + stableFraction(node.node_id) * 0.45)
        positions.set(node.node_id, {
          x: center.x + Math.cos(angle) * radius,
          y: center.y + Math.sin(angle) * radius,
        })
      })
  })

  return positions
}

export function selectedNodePresentation(baseColor: string, baseSize: number) {
  return {
    color: baseColor,
    size: baseSize * 1.38,
    highlighted: false,
    zIndex: 6,
  }
}
