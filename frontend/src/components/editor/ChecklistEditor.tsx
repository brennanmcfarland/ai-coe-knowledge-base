import { useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { edits, type GraphNode } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface Draft {
  key: string
  id: string | null
  text: string
}

interface Props {
  node: GraphNode
  run: (fn: (hash: string) => Promise<unknown>) => Promise<unknown>
  onDone: () => void
}

let draftSeq = 0

export function ChecklistEditor({ node, run, onDone }: Props) {
  const [items, setItems] = useState<Draft[]>(() =>
    node.checklist.map((i) => ({ key: i.id, id: i.id, text: i.text })),
  )
  const move = (idx: number, delta: number) => {
    const next = [...items]
    const [it] = next.splice(idx, 1)
    next.splice(idx + delta, 0, it)
    setItems(next)
  }

  return (
    <div className="flex flex-col gap-2" data-testid="checklist-editor">
      {items.map((it, idx) => (
        <div key={it.key} className="flex items-center gap-1">
          <Input
            value={it.text}
            aria-label={`Checklist item ${idx + 1}`}
            onChange={(e) => setItems(items.map((x) => (x.key === it.key ? { ...x, text: e.target.value } : x)))}
          />
          <Button size="icon" variant="ghost" aria-label="Move up" disabled={idx === 0} onClick={() => move(idx, -1)}>
            <ArrowUp />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Move down"
            disabled={idx === items.length - 1}
            onClick={() => move(idx, 1)}
          >
            <ArrowDown />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Remove item"
            onClick={() => setItems(items.filter((x) => x.key !== it.key))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => setItems([...items, { key: `new-${++draftSeq}`, id: null, text: '' }])}
        >
          <Plus /> Add item
        </Button>
        <Button
          size="sm"
          onClick={async () => {
            await run((h) => edits.setChecklist(h, node.id, items.map(({ id, text }) => ({ id, text }))))
            onDone()
          }}
        >
          Save checklist
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  )
}
