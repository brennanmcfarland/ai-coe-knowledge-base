import { useMemo } from 'react'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Graph as DagreGraph, layout } from '@dagrejs/dagre'
import { CheckCircle2, Circle, Lock } from 'lucide-react'
import type { Graph, GraphEdge, GraphNode } from '@/api'
import { cn } from '@/lib/utils'

const NODE_W = 220
const NODE_H = 64

type StepNodeData = { node: GraphNode; editing: boolean }
type StepNode = Node<StepNodeData, 'step'>

const stateStyles: Record<GraphNode['state'], string> = {
  complete: 'border-emerald-500 bg-emerald-50 dark:bg-emerald-950',
  frontier: 'border-sky-500 bg-sky-50 ring-2 ring-sky-300 dark:bg-sky-950',
  locked: 'border-border bg-muted opacity-60',
}

function StepNodeView({ data }: NodeProps<StepNode>) {
  const { node, editing } = data
  const Icon = node.state === 'complete' ? CheckCircle2 : node.state === 'frontier' ? Circle : Lock
  return (
    <div
      data-testid={`dag-node-${node.id}`}
      data-state={node.state}
      data-scope={node.scope}
      className={cn(
        'relative flex h-full w-full cursor-pointer items-center gap-2 rounded-lg border-2 px-3 text-sm shadow-sm',
        stateStyles[node.state],
        node.scope === 'project' && 'border-l-8 border-l-violet-500',
        node.status === 'proposed' && 'border-dashed border-amber-500 opacity-100',
      )}
    >
      <Handle type="target" position={Position.Top} isConnectable={editing} />
      <Icon className="size-4 shrink-0" aria-hidden />
      <span className="line-clamp-2 font-medium">{node.title}</span>
      {node.status === 'proposed' && <span className="ml-auto text-xs text-amber-600">proposed</span>}
      {!node.grounded && (
        <span
          className="ml-auto shrink-0 rounded bg-amber-100 px-1 text-[10px] text-amber-800 dark:bg-amber-900 dark:text-amber-100"
          title="Not from the knowledge base"
          data-testid={`not-from-kb-${node.id}`}
        >
          Not from KB
        </span>
      )}
      {node.is_new && (
        <span className="absolute -top-2 -right-2 rounded-full bg-sky-600 px-1.5 text-[10px] text-white">new</span>
      )}
      <Handle type="source" position={Position.Bottom} isConnectable={editing} />
    </div>
  )
}

const nodeTypes = { step: StepNodeView }

function layoutGraph(graph: Graph): Map<string, { x: number; y: number }> {
  const g = new DagreGraph()
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 70 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of graph.nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H })
  for (const e of graph.edges) g.setEdge(e.source, e.target)
  layout(g)
  const out = new Map<string, { x: number; y: number }>()
  for (const n of graph.nodes) {
    const p = g.node(n.id)
    out.set(n.id, { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 })
  }
  return out
}

interface DagProps {
  graph: Graph
  editing: boolean
  onOpen: (nodeId: string) => void
  onConnect?: (source: string, target: string) => void
  onDeleteEdge?: (edge: GraphEdge) => void
}

export function Dag({ graph, editing, onOpen, onConnect, onDeleteEdge }: DagProps) {
  const { nodes, edges } = useMemo(() => {
    const pos = layoutGraph(graph)
    const nodes: StepNode[] = graph.nodes.map((n) => ({
      id: n.id,
      type: 'step',
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: { node: n, editing },
      width: NODE_W,
      height: NODE_H,
      draggable: false,
    }))
    const edges: Edge[] = graph.edges.map((e) => ({
      id: `${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      // Bridges stand in for a path through steps this project hides; there's nothing to delete.
      deletable: editing && e.scope !== 'bridge',
      markerEnd: { type: MarkerType.ArrowClosed },
      style:
        e.status === 'proposed'
          ? { strokeDasharray: '6 4', stroke: '#f59e0b' }
          : e.scope === 'bridge'
            ? { strokeDasharray: '2 4' }
            : e.scope === 'project'
              ? { stroke: '#8b5cf6' }
              : undefined,
      data: { status: e.status, scope: e.scope },
    }))
    return { nodes, edges }
  }, [graph, editing])

  return (
    <div className="h-full w-full" data-testid="dag">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        nodesConnectable={editing}
        edgesFocusable={editing}
        elementsSelectable={editing}
        deleteKeyCode={editing ? ['Backspace', 'Delete'] : null}
        onNodeClick={(_, node) => onOpen(node.id)}
        onConnect={(c: Connection) => {
          if (c.source && c.target && c.source !== c.target) onConnect?.(c.source, c.target)
        }}
        onEdgesDelete={(deleted) =>
          deleted.forEach((d) => {
            const e = graph.edges.find((x) => x.source === d.source && x.target === d.target)
            if (e) onDeleteEdge?.(e)
          })
        }
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
