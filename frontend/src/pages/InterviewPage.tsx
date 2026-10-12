import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Loader2, Trash2 } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, ApiError, type Answer, type Draft, type Job, type LlmStatus, type QuestionKind } from '@/api'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(1)} GB`

/** The local model's readiness: consent to download it, its progress, and loading. */
function ModelBanner() {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: ['llm-status'],
    queryFn: api.llmStatus,
    refetchInterval: (q) => (['downloading', 'starting'].includes(q.state.data?.state ?? '') ? 1000 : 5000),
  })
  const warmed = useRef(false)
  const set = (s: LlmStatus) => qc.setQueryData(['llm-status'], s)
  const download = useMutation({ mutationFn: api.llmDownload, onSuccess: set })
  const cancel = useMutation({ mutationFn: api.llmCancelDownload, onSuccess: set })

  // Load the model while the user answers the first questions.
  useEffect(() => {
    if (status.data?.state === 'stopped' && !warmed.current) {
      warmed.current = true
      api.llmWarm().then(set, () => undefined)
    }
  })

  const s = status.data
  if (!s || s.state === 'ready') return null
  return (
    <Alert data-testid="model-banner" className={cn(s.state === 'failed' && 'border-destructive')}>
      {s.state === 'starting' || s.state === 'stopped' ? <Loader2 className="animate-spin" /> : <AlertTriangle />}
      <AlertTitle>
        {
          {
            missing: 'The interview needs the local model',
            downloading: 'Downloading the local model',
            starting: 'Loading the local model…',
            stopped: 'Loading the local model…',
            failed: 'The local model failed',
            ready: '',
          }[s.state]
        }
      </AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        {s.state === 'missing' && (
          <>
            <p>
              Follow-up questions and tailoring run on a local model (llama.cpp and Qwen3-30B), which is about{' '}
              {gb(s.approx_bytes)} to download into <code>.cache/</code>. Nothing leaves your machine. You can also run{' '}
              <code>./build_ontology.sh</code>, which downloads it too.
            </p>
            {s.error && <p className="text-destructive">{s.error}</p>}
            <Button
              size="sm"
              className="self-start"
              onClick={() => download.mutate()}
              disabled={download.isPending}
              data-testid="download-model"
            >
              Download ({gb(s.approx_bytes)})
            </Button>
          </>
        )}
        {s.state === 'downloading' && s.download && (
          <>
            <progress
              className="w-full"
              value={s.download.done}
              max={s.download.total || undefined}
              data-testid="download-progress"
            />
            <p className="text-xs">
              {s.download.file}: {gb(s.download.done)}
              {s.download.total ? ` of ${gb(s.download.total)}` : ''}
            </p>
            <Button size="sm" variant="outline" className="self-start" onClick={() => cancel.mutate()}>
              Cancel (resumes later)
            </Button>
          </>
        )}
        {(s.state === 'starting' || s.state === 'stopped') && <p>This takes a minute or two the first time.</p>}
        {s.state === 'failed' && (
          <>
            <p className="whitespace-pre-wrap">{s.error}</p>
            <Button size="sm" className="self-start" onClick={() => api.llmWarm().then(set)}>
              Try again
            </Button>
          </>
        )}
      </AlertDescription>
    </Alert>
  )
}

interface QuestionProps {
  id: string
  text: string
  kind: QuestionKind
  options: { value: string; label: string }[]
  value: Answer | null | undefined
  disabled: boolean
  required: boolean
  onChange: (value: Answer | null) => void
}

function QuestionInput({ id, text, kind, options, value, disabled, required, onChange }: QuestionProps) {
  const [draftText, setDraftText] = useState(typeof value === 'string' ? value : '')
  const selected = Array.isArray(value) ? value : typeof value === 'string' && value ? [value] : []
  return (
    <fieldset className="flex flex-col gap-2" data-testid={`q-${id}`} disabled={disabled}>
      <legend className="mb-2 font-medium">
        {text}
        {!required && <span className="ml-2 text-xs font-normal text-muted-foreground">(optional)</span>}
      </legend>
      {kind === 'text' && (
        <Textarea
          value={draftText}
          onChange={(e) => setDraftText(e.target.value)}
          onBlur={() => draftText !== (value ?? '') && onChange(draftText.trim() ? draftText : null)}
          data-testid={`text-${id}`}
        />
      )}
      {kind === 'single' && (
        <div className="flex flex-wrap gap-2">
          {options.map((o) => (
            <Button
              key={o.value}
              type="button"
              size="sm"
              variant={selected.includes(o.value) ? 'default' : 'outline'}
              aria-pressed={selected.includes(o.value)}
              onClick={() => onChange(o.value)}
              data-testid={`opt-${id}-${o.value}`}
            >
              {selected.includes(o.value) && <Check />} {o.label}
            </Button>
          ))}
        </div>
      )}
      {kind === 'multi' && (
        <div className="grid gap-2 sm:grid-cols-2">
          {options.map((o) => (
            <div key={o.value} className="flex items-center gap-2">
              <Checkbox
                id={`${id}-${o.value}`}
                checked={selected.includes(o.value)}
                data-testid={`opt-${id}-${o.value}`}
                onCheckedChange={(v) => {
                  const next = v === true ? [...selected, o.value] : selected.filter((x) => x !== o.value)
                  onChange(next.length ? next : null)
                }}
              />
              <Label htmlFor={`${id}-${o.value}`} className="font-normal">
                {o.label}
              </Label>
            </div>
          ))}
        </div>
      )}
    </fieldset>
  )
}

export function InterviewPage() {
  const iid = useParams().iid ?? ''
  const navigate = useNavigate()
  const qc = useQueryClient()
  const draftKey = ['interview', iid]
  const draft = useQuery({ queryKey: draftKey, queryFn: () => api.interview(iid), retry: false })
  const defn = useQuery({ queryKey: ['interview-def', false], queryFn: () => api.interviewDef(false) })
  const [watched, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState<string | null>(null)
  const pendingText = useRef<Promise<unknown>[]>([])
  // Resume watching a job that was running when the page was (re)loaded.
  const job = watched ?? (draft.data?.job?.state === 'running' ? draft.data.job : null)

  useEffect(() => {
    if (job?.state !== 'running') return
    const timer = setInterval(async () => {
      try {
        const next = await api.interviewJob(iid)
        setJob(next)
        if (next.state === 'succeeded') {
          if (next.result?.project_id !== undefined) {
            qc.invalidateQueries({ queryKey: ['projects'] })
            qc.invalidateQueries({ queryKey: ['graph'] })
            qc.invalidateQueries({ queryKey: ['interviews'] })
            navigate(`/projects/${next.result.project_id}`)
          } else {
            await qc.invalidateQueries({ queryKey: draftKey })
          }
        }
      } catch {
        // transient; keep polling
      }
    }, 500)
    return () => clearInterval(timer)
  })

  const save = async (questionId: string, value: Answer | null) => {
    setError(null)
    try {
      const updated = await api.answer(iid, questionId, value)
      qc.setQueryData<Draft>(draftKey, updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const advance = async () => {
    setError(null)
    await Promise.all(pendingText.current)
    pendingText.current = []
    try {
      const r = await api.advance(iid)
      if (r.job_started) setJob({ state: 'running', messages: [], next: 0, error: null, result: null })
      else if (r.draft) qc.setQueryData(draftKey, r.draft)
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : String(e))
    }
  }

  if (draft.error) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <p>This interview has finished or was discarded.</p>
        <Link to="/projects" className="text-sky-600 underline">
          Back to projects
        </Link>
      </div>
    )
  }
  if (!draft.data || !defn.data) return <p className="p-6 text-muted-foreground">Loading…</p>

  const d = draft.data
  const sections = defn.data.sections
  const sectionIdx = Math.min(d.section, sections.length - 1)
  const section = sections[sectionIdx]
  const questions = defn.data.questions.filter((q) => q.section === section.id)
  const followups = d.followups.filter((f) => f.section === section.id)
  const running = job?.state === 'running'
  const last = sectionIdx === sections.length - 1
  const finishing = last && (d.stage === 'followups' || section.max_followups === 0)
  const missing = d.stage === 'questions' && questions.some((q) => q.kind !== 'text' && !d.answers[q.id]?.length)

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 overflow-y-auto p-6" style={{ maxHeight: '100%' }}>
      <header className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">
          {d.project_id === null ? 'New application' : 'Re-tailoring an existing project'}
        </p>
        <div className="flex items-center gap-2">
          <Input
            className="max-w-sm text-lg font-semibold"
            aria-label="Project name"
            value={name ?? d.name}
            disabled={d.project_id !== null}
            onChange={(e) => setName(e.target.value)}
            onBlur={async () => {
              if (name && name.trim() && name !== d.name) qc.setQueryData(draftKey, await api.renameInterview(iid, name))
            }}
            data-testid="interview-name"
          />
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            disabled={running}
            onClick={async () => {
              if (!confirm('Discard this interview and its answers?')) return
              await api.deleteInterview(iid)
              qc.invalidateQueries({ queryKey: ['interviews'] })
              navigate(d.project_id === null ? '/projects' : `/projects/${d.project_id}`)
            }}
          >
            <Trash2 /> Discard
          </Button>
        </div>
        <ol className="flex flex-wrap gap-2 text-sm" data-testid="interview-steps">
          {sections.map((s, i) => (
            <li
              key={s.id}
              className={cn(
                'rounded-full border px-3 py-1',
                i === sectionIdx && 'border-sky-500 bg-sky-50 font-medium dark:bg-sky-950',
                i < sectionIdx && 'text-muted-foreground line-through',
              )}
            >
              {i + 1}. {s.title}
            </li>
          ))}
        </ol>
      </header>

      <ModelBanner />

      <section className="flex flex-col gap-6" data-testid="interview-section" data-section={section.id}>
        <h2 className="text-xl font-semibold">{section.title}</h2>
        {d.stage === 'questions' &&
          questions.map((q) => (
            <QuestionInput
              key={`${d.id}-${q.id}`}
              id={q.id}
              text={q.text}
              kind={q.kind}
              options={q.options}
              value={d.answers[q.id]}
              required={q.kind !== 'text'}
              disabled={running}
              onChange={(v) => {
                const p = save(q.id, v)
                if (q.kind === 'text') pendingText.current.push(p)
              }}
            />
          ))}
        {d.stage === 'followups' && (
          <>
            <p className="text-sm text-muted-foreground" data-testid="followups-intro">
              A few follow-up questions, based on your answers and what the knowledge base covers. All optional.
            </p>
            {followups.map((f) => (
              <QuestionInput
                key={`${d.id}-${f.id}`}
                id={f.id}
                text={f.text}
                kind={f.kind}
                options={f.options.map((o) => ({ value: o, label: o }))}
                value={f.answer}
                required={false}
                disabled={running}
                onChange={(v) => {
                  const p = save(f.id, v)
                  if (f.kind === 'text') pendingText.current.push(p)
                }}
              />
            ))}
          </>
        )}
      </section>

      {error && (
        <Alert variant="destructive" data-testid="interview-error">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {job && job.state !== 'idle' && (job.state !== 'succeeded' || running) && (
        <div className="flex flex-col gap-1 rounded-lg border p-3 text-sm" data-testid="interview-job">
          {running && (
            <div className="flex items-center gap-2 font-medium">
              <Loader2 className="size-4 animate-spin" /> Working…
            </div>
          )}
          {job.messages.map((m, i) => (
            <p key={i} className="text-muted-foreground">
              {m}
            </p>
          ))}
        </div>
      )}

      {job?.state === 'failed' && (
        <Alert variant="destructive" data-testid="interview-failed">
          <AlertTitle>The model couldn't finish this step</AlertTitle>
          <AlertDescription className="flex flex-col gap-2">
            <p className="whitespace-pre-wrap">{job.error}</p>
            <p>Your answers are saved. Retry when the model is available.</p>
            <Button size="sm" className="self-start" onClick={advance} data-testid="interview-retry">
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {job?.state !== 'failed' && (
        <div className="flex gap-2">
          <Button onClick={advance} disabled={running || missing} data-testid="interview-next">
            {finishing ? (d.project_id === null ? 'Finish and create project' : 'Finish and review changes') : 'Next'}
          </Button>
        </div>
      )}
    </div>
  )
}
