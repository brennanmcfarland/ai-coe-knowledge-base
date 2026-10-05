import { Check, X } from 'lucide-react'
import { edits, type Graph } from '@/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

interface Props {
  graph: Graph
  run: (fn: (hash: string) => Promise<unknown>) => Promise<unknown>
}

export function ProposalsDialog({ graph, run }: Props) {
  const nodes = graph.nodes.filter((n) => n.status === 'proposed')
  const edges = graph.edges.filter((e) => e.status === 'proposed')
  const title = (id: string) => graph.nodes.find((n) => n.id === id)?.title ?? id

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" data-testid="proposals-button">
          Proposals <Badge variant={graph.proposals ? 'default' : 'secondary'}>{graph.proposals}</Badge>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[80dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Proposed changes from the last build</DialogTitle>
        </DialogHeader>
        {graph.proposals === 0 && <p className="text-sm text-muted-foreground">Nothing to review.</p>}
        <ul className="flex flex-col gap-2">
          {nodes.map((n) => (
            <li key={n.id} className="flex items-center gap-2 text-sm" data-testid={`proposal-node-${n.id}`}>
              <Badge variant="outline">node</Badge>
              <span className="flex-1">{n.title}</span>
              <Button size="icon" variant="ghost" aria-label="Accept" onClick={() => run((h) => edits.resolveNode(h, n.id, true))}>
                <Check />
              </Button>
              <Button size="icon" variant="ghost" aria-label="Reject" onClick={() => run((h) => edits.resolveNode(h, n.id, false))}>
                <X />
              </Button>
            </li>
          ))}
          {edges.map((e) => (
            <li
              key={`${e.source}->${e.target}`}
              className="flex items-center gap-2 text-sm"
              data-testid={`proposal-edge-${e.source}-${e.target}`}
            >
              <Badge variant="outline">edge</Badge>
              <span className="flex-1">
                {title(e.source)} → {title(e.target)}
              </span>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Accept"
                onClick={() => run((h) => edits.resolveEdge(h, e.source, e.target, true))}
              >
                <Check />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Reject"
                onClick={() => run((h) => edits.resolveEdge(h, e.source, e.target, false))}
              >
                <X />
              </Button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  )
}
