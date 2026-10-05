import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, edits } from '@/api'
import { Dag } from '@/components/Dag'
import { ProposalsDialog } from '@/components/editor/ProposalsDialog'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useEditMode } from '@/lib/editMode'
import { frontier } from '@/lib/frontier'
import { useGraph, useOntologyEdit } from '@/lib/hooks'

export function GraphPage() {
  const pid = Number(useParams().pid)
  const navigate = useNavigate()
  const { editing } = useEditMode()
  const project = useQuery({ queryKey: ['project', pid], queryFn: () => api.project(pid) })
  const graph = useGraph(pid)
  const edit = useOntologyEdit(pid)
  const [newTitle, setNewTitle] = useState('')

  if (graph.error) {
    return (
      <Alert variant="destructive" className="m-6 w-auto">
        <AlertDescription>{graph.error.message}</AlertDescription>
      </Alert>
    )
  }
  if (!graph.data) return <p className="p-6 text-muted-foreground">Loading…</p>

  const g = graph.data
  const done = g.nodes.filter((n) => n.completed).length
  const next = frontier(g)[0]

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-2">
        <h1 className="text-lg font-semibold" data-testid="project-title">
          {project.data?.name}
        </h1>
        <span className="text-sm text-muted-foreground" data-testid="progress-count">
          {done} / {g.nodes.filter((n) => n.status === 'accepted').length} steps complete
        </span>
        {next && (
          <Button size="sm" asChild data-testid="next-step">
            <Link to={`/projects/${pid}/nodes/${encodeURIComponent(next.id)}`}>
              Next: {next.title} <ArrowRight />
            </Link>
          </Button>
        )}
        {editing && (
          <div className="ml-auto flex items-center gap-2">
            <form
              className="flex gap-2"
              onSubmit={async (e) => {
                e.preventDefault()
                const title = newTitle.trim()
                if (!title) return
                await edit.run((h) => edits.createNode(h, title))
                setNewTitle('')
              }}
            >
              <Input
                className="h-8 w-48"
                placeholder="New step title"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                data-testid="new-node-title"
              />
              <Button size="sm" type="submit" disabled={!newTitle.trim() || edit.pending}>
                Add step
              </Button>
            </form>
            <ProposalsDialog graph={g} run={edit.run} />
          </div>
        )}
      </div>
      {edit.error && (
        <Alert variant="destructive" className="mx-4 mt-2 w-auto" data-testid="edit-error">
          <AlertDescription>{edit.error}</AlertDescription>
        </Alert>
      )}
      {editing && (
        <p className="px-4 pt-2 text-xs text-muted-foreground">
          Edit mode: drag from a node's bottom handle to another node to add a prerequisite; select an edge and press
          Delete to remove it. Dashed amber items are proposals from the last build.
        </p>
      )}
      <div className="min-h-0 flex-1">
        {g.nodes.length === 0 ? (
          <p className="p-6 text-muted-foreground">
            No steps yet. Sync from ClickUp, then run <code>./build_ontology.sh</code>.
          </p>
        ) : (
          <Dag
            graph={g}
            editing={editing}
            onOpen={(id) => navigate(`/projects/${pid}/nodes/${encodeURIComponent(id)}`)}
            onConnect={(s, t) => edit.run((h) => edits.addEdge(h, s, t))}
            onDeleteEdge={(s, t) => edit.run((h) => edits.deleteEdge(h, s, t))}
          />
        )}
      </div>
    </div>
  )
}
