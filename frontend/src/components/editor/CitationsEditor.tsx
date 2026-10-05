import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Trash2 } from 'lucide-react'
import { api, edits, type Citation, type GraphNode } from '@/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

interface Props {
  node: GraphNode
  run: (fn: (hash: string) => Promise<unknown>) => Promise<unknown>
}

function ChunkPicker({ onPick, onClose }: { onPick: (chunkId: string) => void; onClose: () => void }) {
  const [q, setQ] = useState('')
  const hits = useQuery({ queryKey: ['chunks', q], queryFn: () => api.searchChunks(q) })
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[80dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Choose a source passage</DialogTitle>
        </DialogHeader>
        <Input placeholder="Search the corpus" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        <ul className="flex flex-col gap-2">
          {hits.data?.map((h) => (
            <li key={h.id}>
              <button
                type="button"
                className="w-full rounded border p-2 text-left text-sm hover:bg-muted"
                onClick={() => onPick(h.id)}
              >
                <div className="font-medium">{h.doc_title}</div>
                <div className="line-clamp-3 text-muted-foreground">{h.text}</div>
              </button>
            </li>
          ))}
          {hits.data?.length === 0 && <p className="text-sm text-muted-foreground">No matches.</p>}
        </ul>
      </DialogContent>
    </Dialog>
  )
}

/** Remove a citation, re-attach it to a different passage (keeping its [n] marker), or add one. */
export function CitationsEditor({ node, run }: Props) {
  const [picking, setPicking] = useState<{ replace: Citation | null } | null>(null)
  const save = (items: { chunk_id: string; n: number | null }[]) =>
    run((h) => edits.setCitations(h, node.id, items))
  const current = node.citations.map((c) => ({ chunk_id: c.chunk_id, n: c.n as number | null }))

  return (
    <div className="flex flex-col gap-2" data-testid="citations-editor">
      {node.citations.map((c) => (
        <div key={c.n} className="flex items-start gap-2 rounded border p-2 text-sm">
          <span className="font-semibold">[{c.n}]</span>
          <div className="flex-1">
            <div className="font-medium">{c.doc_title}</div>
            <div className="line-clamp-2 text-muted-foreground">{c.excerpt}</div>
            {c.broken && (
              <div className="mt-1 flex items-center gap-1 text-amber-600" data-testid={`broken-citation-${c.n}`}>
                <AlertTriangle className="size-3" /> Source passage no longer exists in the corpus
              </div>
            )}
          </div>
          <Button size="sm" variant="outline" onClick={() => setPicking({ replace: c })}>
            Re-attach
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Remove citation ${c.n}`}
            onClick={() => save(current.filter((x) => x.n !== c.n))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="outline" className="self-start" onClick={() => setPicking({ replace: null })}>
        Add citation
      </Button>
      {picking && (
        <ChunkPicker
          onClose={() => setPicking(null)}
          onPick={async (chunkId) => {
            const target = picking.replace
            const items = target
              ? current.map((x) => (x.n === target.n ? { chunk_id: chunkId, n: target.n } : x))
              : [...current, { chunk_id: chunkId, n: null }]
            setPicking(null)
            await save(items)
          }}
        />
      )}
    </div>
  )
}
