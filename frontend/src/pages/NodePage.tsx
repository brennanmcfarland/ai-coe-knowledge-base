import { useState } from 'react'
import { ArrowLeft, ArrowRight, Pencil } from 'lucide-react'
import { Link, useParams } from 'react-router'
import type { Citation } from '@/api'
import { CitationPanel } from '@/components/CitationPanel'
import { ChecklistEditor } from '@/components/editor/ChecklistEditor'
import { CitationsEditor } from '@/components/editor/CitationsEditor'
import { NodeEditTools } from '@/components/editor/NodeEditTools'
import { Summary } from '@/components/Summary'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { useEditMode } from '@/lib/editMode'
import { frontier } from '@/lib/frontier'
import { useGraph, useOntologyEdit, useProgressMutations } from '@/lib/hooks'

const stateLabel = { complete: 'Complete', frontier: 'Ready to start', locked: 'Prerequisites pending' } as const

export function NodePage() {
  const params = useParams()
  const pid = Number(params.pid)
  const nodeId = params.nid ?? ''
  const { editing } = useEditMode()
  const graph = useGraph(pid)
  const { setChecked, setCompleted } = useProgressMutations(pid)
  const edit = useOntologyEdit(pid)
  const [citation, setCitation] = useState<Citation | null>(null)
  const [editingChecklist, setEditingChecklist] = useState(false)

  if (graph.error) {
    return (
      <Alert variant="destructive" className="m-6 w-auto">
        <AlertDescription>{graph.error.message}</AlertDescription>
      </Alert>
    )
  }
  if (!graph.data) return <p className="p-6 text-muted-foreground">Loading…</p>

  const g = graph.data
  const node = g.nodes.find((n) => n.id === nodeId)
  if (!node) {
    return (
      <div className="p-6">
        <p>This step doesn't exist (it may have been merged or deleted).</p>
        <Link to={`/projects/${pid}`} className="text-sky-600 underline">
          Back to the graph
        </Link>
      </div>
    )
  }

  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  const nodeLink = (id: string) => `/projects/${pid}/nodes/${encodeURIComponent(id)}`
  const front = frontier(g)
  const idx = front.findIndex((n) => n.id === node.id)
  // Previous/next walk the frontier; from a non-frontier node, "next" jumps to the first frontier step.
  const prev = idx > 0 ? front[idx - 1] : undefined
  const next = idx >= 0 ? front[idx + 1] : front[0]

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 overflow-y-auto p-6" style={{ maxHeight: '100%' }}>
      <nav className="flex items-center gap-2 text-sm">
        <Link to={`/projects/${pid}`} className="text-muted-foreground hover:underline" data-testid="back-to-graph">
          ← Graph
        </Link>
        <div className="ml-auto flex gap-2">
          {prev && (
            <Button size="sm" variant="outline" asChild>
              <Link to={nodeLink(prev.id)} data-testid="prev-frontier">
                <ArrowLeft /> {prev.title}
              </Link>
            </Button>
          )}
          {next && (
            <Button size="sm" variant="outline" asChild>
              <Link to={nodeLink(next.id)} data-testid="next-frontier">
                {next.title} <ArrowRight />
              </Link>
            </Button>
          )}
        </div>
      </nav>

      {edit.error && (
        <Alert variant="destructive" data-testid="edit-error">
          <AlertDescription>{edit.error}</AlertDescription>
        </Alert>
      )}

      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Badge variant={node.state === 'frontier' ? 'default' : 'secondary'} data-testid="node-state">
            {stateLabel[node.state]}
          </Badge>
          {node.status === 'proposed' && <Badge variant="outline">Proposed</Badge>}
        </div>
        <h1 className="text-2xl font-semibold" data-testid="node-title">
          {node.title}
        </h1>
        {node.predecessors.length > 0 && (
          <p className="text-sm text-muted-foreground">
            After:{' '}
            {node.predecessors.map((p, i) => (
              <span key={p}>
                {i > 0 && ', '}
                <Link to={nodeLink(p)} className="underline">
                  {byId.get(p)?.title ?? p}
                </Link>
              </span>
            ))}
          </p>
        )}
      </header>

      {editing && <NodeEditTools key={node.id + node.title} pid={pid} node={node} graph={g} run={edit.run} />}

      <section>
        <Summary summary={node.summary} citations={node.citations} onCite={setCitation} />
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Checklist</h2>
          {editing && !editingChecklist && (
            <Button size="sm" variant="ghost" onClick={() => setEditingChecklist(true)} data-testid="edit-checklist">
              <Pencil /> Edit
            </Button>
          )}
        </div>
        {editing && editingChecklist ? (
          <ChecklistEditor node={node} run={edit.run} onDone={() => setEditingChecklist(false)} />
        ) : node.checklist.length === 0 ? (
          <p className="text-sm text-muted-foreground">No checklist for this step.</p>
        ) : (
          <ul className="flex flex-col gap-2" data-testid="checklist">
            {node.checklist.map((item) => (
              <li key={item.id} className="flex items-start gap-2">
                <Checkbox
                  id={`item-${item.id}`}
                  checked={item.checked}
                  data-testid={`check-${item.id}`}
                  onCheckedChange={(v) => setChecked.mutate({ nodeId: node.id, itemId: item.id, checked: v === true })}
                />
                <Label htmlFor={`item-${item.id}`} className="leading-snug font-normal">
                  {item.text}
                </Label>
              </li>
            ))}
          </ul>
        )}
      </section>

      {node.citations.length > 0 && !editing && (
        <section className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold">Sources</h2>
          <ol className="flex flex-col gap-1 text-sm">
            {node.citations.map((c) => (
              <li key={c.n}>
                <button
                  type="button"
                  className="text-left text-sky-600 hover:underline"
                  onClick={() => setCitation(c)}
                  data-testid={`source-${c.n}`}
                >
                  [{c.n}] {c.doc_title || c.url}
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}

      {editing && (
        <section className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold">Citations</h2>
          <CitationsEditor node={node} run={edit.run} />
        </section>
      )}

      {node.status === 'accepted' && (
        <section className="flex items-center gap-3 rounded-lg border p-4">
          <Checkbox
            id="mark-complete"
            checked={node.completed}
            data-testid="mark-complete"
            onCheckedChange={(v) => setCompleted.mutate({ nodeId: node.id, completed: v === true })}
          />
          <Label htmlFor="mark-complete" className="text-base">
            Mark this step complete
          </Label>
          {node.state === 'locked' && (
            <span className="text-xs text-muted-foreground">(you can, but its prerequisites aren't done yet)</span>
          )}
        </section>
      )}

      <CitationPanel citation={citation} onClose={() => setCitation(null)} />
    </div>
  )
}
