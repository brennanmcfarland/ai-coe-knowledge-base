import { useState } from 'react'
import { Check, GitMerge, Trash2, X } from 'lucide-react'
import { useNavigate } from 'react-router'
import { edits, type Graph, type GraphNode } from '@/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface Props {
  pid: number
  node: GraphNode
  graph: Graph
  run: (fn: (hash: string) => Promise<unknown>) => Promise<unknown>
}

export function NodeEditTools({ pid, node, graph, run }: Props) {
  const navigate = useNavigate()
  const [title, setTitle] = useState(node.title)
  const [mergeInto, setMergeInto] = useState<string>('')
  const [mergeOpen, setMergeOpen] = useState(false)
  const others = graph.nodes.filter((n) => n.id !== node.id)

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed p-3" data-testid="node-edit-tools">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (title.trim() && title !== node.title) run((h) => edits.renameNode(h, node.id, title.trim()))
        }}
      >
        <Input value={title} onChange={(e) => setTitle(e.target.value)} className="w-64" aria-label="Step title" />
        <Button type="submit" size="sm" variant="outline" disabled={!title.trim() || title === node.title}>
          Rename
        </Button>
      </form>

      {node.status === 'proposed' && (
        <>
          <Button size="sm" onClick={() => run((h) => edits.resolveNode(h, node.id, true))} data-testid="accept-node">
            <Check /> Accept proposal
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              await run((h) => edits.resolveNode(h, node.id, false))
              navigate(`/projects/${pid}`)
            }}
          >
            <X /> Reject
          </Button>
        </>
      )}

      <Dialog open={mergeOpen} onOpenChange={setMergeOpen}>
        <DialogTrigger asChild>
          <Button size="sm" variant="outline" data-testid="merge-button">
            <GitMerge /> Merge into…
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Merge “{node.title}” into another step</DialogTitle>
            <DialogDescription>
              Its checklist, citations, edges and every project's progress move to the chosen step. This step is then
              removed.
            </DialogDescription>
          </DialogHeader>
          <Select value={mergeInto} onValueChange={setMergeInto}>
            <SelectTrigger className="w-full" data-testid="merge-target">
              <SelectValue placeholder="Choose a step" />
            </SelectTrigger>
            <SelectContent>
              {others.map((n) => (
                <SelectItem key={n.id} value={n.id}>
                  {n.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DialogFooter>
            <Button
              disabled={!mergeInto}
              data-testid="merge-confirm"
              onClick={async () => {
                setMergeOpen(false)
                const result = await run((h) => edits.mergeNode(h, node.id, mergeInto))
                if (result) navigate(`/projects/${pid}/nodes/${encodeURIComponent(mergeInto)}`)
              }}
            >
              Merge
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Button
        size="sm"
        variant="destructive"
        data-testid="delete-node"
        onClick={async () => {
          if (!confirm(`Delete “${node.title}”? Progress on it is hidden, not erased.`)) return
          const result = await run((h) => edits.deleteNode(h, node.id))
          if (result) navigate(`/projects/${pid}`)
        }}
      >
        <Trash2 /> Delete
      </Button>
      {node.locked_fields.length > 0 && (
        <span className="text-xs text-muted-foreground">Locked against rebuilds: {node.locked_fields.join(', ')}</span>
      )}
    </div>
  )
}
