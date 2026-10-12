import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { api, ApiError, interviewEdits, type EditResult } from '@/api'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

/** Edit mode: accept or reject the interview questions and facet values builds proposed. */
export function InterviewQuestionsDialog() {
  const qc = useQueryClient()
  const key = ['interview-def', true]
  const defn = useQuery({ queryKey: key, queryFn: () => api.interviewDef(true) })
  const [error, setError] = useState<string | null>(null)
  const resolve = useMutation({
    mutationFn: (fn: (hash: string) => Promise<EditResult>) => {
      const hash = defn.data?.hash
      if (!hash) throw new Error('Questions not loaded')
      return fn(hash)
    },
    onMutate: () => setError(null),
    onError: (e) =>
      setError(
        e instanceof ApiError && e.code === 'stale'
          ? 'interview.yaml changed since you loaded it. Reloaded — please retry.'
          : e.message,
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: ['interview-def'] }),
  })

  const d = defn.data
  const proposedQuestions = d?.questions.filter((q) => q.status === 'proposed') ?? []
  const proposedOptions =
    d?.questions.flatMap((q) =>
      q.status === 'accepted' ? q.options.filter((o) => o.status === 'proposed').map((o) => ({ q, o })) : [],
    ) ?? []

  const buttons = (onAccept: () => void, onReject: () => void) => (
    <>
      <Button size="icon" variant="ghost" aria-label="Accept" onClick={onAccept}>
        <Check />
      </Button>
      <Button size="icon" variant="ghost" aria-label="Reject" onClick={onReject}>
        <X />
      </Button>
    </>
  )

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" data-testid="interview-questions-button">
          Interview questions <Badge variant={d?.proposals ? 'default' : 'secondary'}>{d?.proposals ?? 0}</Badge>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[80dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Proposed interview questions</DialogTitle>
          <DialogDescription>
            Builds propose questions and answer options from the knowledge base. Accepted ones become facets that tag
            steps and passages. Everything else is edited in <code>data/interview.yaml</code>.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {d?.proposals === 0 && <p className="text-sm text-muted-foreground">Nothing to review.</p>}
        <ul className="flex flex-col gap-2">
          {proposedQuestions.map((q) => (
            <li key={q.id} className="flex items-start gap-2 text-sm" data-testid={`proposed-question-${q.id}`}>
              <Badge variant="outline">question</Badge>
              <span className="flex-1">
                {q.text}
                <span className="block text-xs text-muted-foreground">
                  {q.options.map((o) => o.label).join(' · ')}
                </span>
              </span>
              {buttons(
                () => resolve.mutate((h) => interviewEdits.resolveQuestion(h, q.id, true)),
                () => resolve.mutate((h) => interviewEdits.resolveQuestion(h, q.id, false)),
              )}
            </li>
          ))}
          {proposedOptions.map(({ q, o }) => (
            <li key={`${q.id}/${o.value}`} className="flex items-center gap-2 text-sm">
              <Badge variant="outline">option</Badge>
              <span className="flex-1">
                {o.label} <span className="text-xs text-muted-foreground">for “{q.text}”</span>
              </span>
              {buttons(
                () => resolve.mutate((h) => interviewEdits.resolveOption(h, q.id, o.value, true)),
                () => resolve.mutate((h) => interviewEdits.resolveOption(h, q.id, o.value, false)),
              )}
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  )
}
