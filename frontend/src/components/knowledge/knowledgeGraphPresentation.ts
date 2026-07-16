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
    const ringRadius = ring * 12
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
    const localRadius = Math.max(1.8, 1.45 * Math.sqrt(group.length))
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
