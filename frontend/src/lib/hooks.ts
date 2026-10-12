import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError, type Graph } from '@/api'
import { useEditMode } from './editMode'

export function graphKey(pid: number, editing: boolean) {
  return ['graph', pid, editing] as const
}

export function useGraph(pid: number) {
  const { editing } = useEditMode()
  return useQuery({ queryKey: graphKey(pid, editing), queryFn: () => api.graph(pid, editing) })
}

function patchGraph(graph: Graph | undefined, nodeId: string, fn: (n: Graph['nodes'][number]) => void) {
  if (!graph) return graph
  const next = structuredClone(graph)
  const node = next.nodes.find((n) => n.id === nodeId)
  if (node) fn(node)
  return next
}

/** Optimistic progress toggles: update the cached graph, roll back on failure. */
export function useProgressMutations(pid: number) {
  const qc = useQueryClient()
  const { editing } = useEditMode()
  const key = graphKey(pid, editing)

  const optimistic = <V,>(apply: (g: Graph | undefined, v: V) => Graph | undefined) => ({
    onMutate: async (vars: V) => {
      await qc.cancelQueries({ queryKey: key })
      const prev = qc.getQueryData<Graph>(key)
      qc.setQueryData<Graph | undefined>(key, (g) => apply(g, vars))
      return { prev }
    },
    onError: (_e: unknown, _v: V, ctx: { prev: Graph | undefined } | undefined) => {
      qc.setQueryData(key, ctx?.prev)
    },
    // Completion changes the frontier, which is computed server-side.
    onSettled: () => qc.invalidateQueries({ queryKey: ['graph', pid] }),
  })

  const setChecked = useMutation({
    mutationFn: (v: { nodeId: string; itemId: string; checked: boolean }) =>
      api.setChecked(pid, v.nodeId, v.itemId, v.checked),
    ...optimistic<{ nodeId: string; itemId: string; checked: boolean }>((g, v) =>
      patchGraph(g, v.nodeId, (n) => {
        const item = n.checklist.find((i) => i.id === v.itemId)
        if (item) item.checked = v.checked
      }),
    ),
  })

  const setCompleted = useMutation({
    mutationFn: (v: { nodeId: string; completed: boolean }) => api.setCompleted(pid, v.nodeId, v.completed),
    ...optimistic<{ nodeId: string; completed: boolean }>((g, v) =>
      patchGraph(g, v.nodeId, (n) => {
        n.completed = v.completed
        n.state = v.completed ? 'complete' : n.state === 'complete' ? 'frontier' : n.state
      }),
    ),
  })

  return { setChecked, setCompleted }
}

export type EditRun = (fn: (hash: string) => Promise<unknown>) => Promise<unknown>

/**
 * Runs an ontology edit with the graph's current hash: `run` for the global ontology, `runProject`
 * for the project's own overlay (its generated steps and proposals). On a stale-hash 409 it
 * refetches and reports the conflict instead of retrying, since the user should see what changed.
 */
export function useOntologyEdit(pid: number) {
  const qc = useQueryClient()
  const { editing } = useEditMode()
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: async ({ fn, project }: { fn: (hash: string) => Promise<unknown>; project: boolean }) => {
      const graph = qc.getQueryData<Graph>(graphKey(pid, editing))
      if (!graph) throw new Error('Graph not loaded')
      const hash = project ? graph.overlay_hash : graph.hash
      if (hash === null) throw new Error('This project isn’t tailored')
      return fn(hash)
    },
    onMutate: () => setError(null),
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'stale') {
        setError('The ontology changed since you loaded it (another tab or a rebuild). Reloaded — please retry.')
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['graph'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  // Errors are surfaced through `error`; resolve to undefined so callers needn't catch.
  const run: EditRun = (fn) => mutation.mutateAsync({ fn, project: false }).catch(() => undefined)
  const runProject: EditRun = (fn) => mutation.mutateAsync({ fn, project: true }).catch(() => undefined)
  return { run, runProject, pending: mutation.isPending, error, clearError: () => setError(null) }
}
