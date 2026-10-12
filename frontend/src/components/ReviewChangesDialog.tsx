import { Check, X } from 'lucide-react'
import { projectEdits, type Graph, type ProposalKind } from '@/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import type { EditRun } from '@/lib/hooks'

const kindLabel: Record<ProposalKind, string> = {
  include: 'add step',
  exclude: 'remove step',
  add_node: 'new project step',
  remove_node: 'remove project step',
  add_edge: 'new order',
  new: 'new in knowledge base',
}

interface Props {
  pid: number
  graph: Graph
  /** The project-overlay runner (`useOntologyEdit(pid).runProject`). */
  run: EditRun
}

/** Pending changes to a tailored project: a re-run interview's diff, and global steps that started
 * matching the project since it was tailored. Nothing changes until the user accepts it. */
export function ReviewChangesDialog({ pid, graph, run }: Props) {
  const proposals = graph.project_proposals
  const ops = projectEdits(pid)

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" data-testid="review-changes-button">
          Review changes <Badge variant={proposals.length ? 'default' : 'secondary'}>{proposals.length}</Badge>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[80dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Changes to this project</DialogTitle>
          <DialogDescription>
            From re-running the interview, or new knowledge-base steps that match your answers. Steps you're working on
            only change when you accept.
          </DialogDescription>
        </DialogHeader>
        {proposals.length === 0 && <p className="text-sm text-muted-foreground">Nothing to review.</p>}
        <ul className="flex flex-col gap-2">
          {proposals.map((p) => (
            <li key={p.key} className="flex items-center gap-2 text-sm" data-testid={`project-proposal-${p.key}`}>
              <Badge variant="outline">{kindLabel[p.kind]}</Badge>
              <span className="flex-1">
                {p.title}
                {!p.grounded && <span className="ml-2 text-xs text-amber-700">Not from KB</span>}
              </span>
              <Button
                size="sm"
                variant="ghost"
                aria-label={p.kind === 'new' ? 'Keep' : 'Accept'}
                onClick={() => run((h) => ops.resolveProposal(h, p.key, true))}
              >
                <Check /> {p.kind === 'new' ? 'Keep' : 'Accept'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={p.kind === 'new' ? 'Exclude' : 'Reject'}
                onClick={() => run((h) => ops.resolveProposal(h, p.key, false))}
              >
                <X /> {p.kind === 'new' ? 'Exclude' : 'Reject'}
              </Button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  )
}
