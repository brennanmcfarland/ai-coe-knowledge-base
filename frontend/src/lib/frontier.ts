import type { Graph, GraphNode } from '@/api'

/** Nodes in a stable topological order (Kahn's algorithm, ties broken by graph order). */
export function topoOrder(graph: Graph): GraphNode[] {
  const indeg = new Map(graph.nodes.map((n) => [n.id, 0]))
  for (const e of graph.edges) indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1)
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const queue = graph.nodes.filter((n) => indeg.get(n.id) === 0)
  const out: GraphNode[] = []
  while (queue.length) {
    const n = queue.shift()!
    out.push(n)
    for (const e of graph.edges) {
      if (e.source !== n.id) continue
      const d = (indeg.get(e.target) ?? 0) - 1
      indeg.set(e.target, d)
      const t = byId.get(e.target)
      if (d === 0 && t) queue.push(t)
    }
  }
  return out
}

/** Frontier nodes in topological order, for previous/next navigation. */
export function frontier(graph: Graph): GraphNode[] {
  return topoOrder(graph).filter((n) => n.state === 'frontier')
}
