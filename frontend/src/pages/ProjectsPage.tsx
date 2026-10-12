import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Wand2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { api } from '@/api'
import { InterviewQuestionsDialog } from '@/components/editor/InterviewQuestionsDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useEditMode } from '@/lib/editMode'

export function ProjectsPage() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { editing } = useEditMode()
  const [name, setName] = useState('')
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const drafts = useQuery({ queryKey: ['interviews'], queryFn: api.interviews })
  const start = useMutation({
    mutationFn: () => api.createInterview(name.trim()),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ['interviews'] })
      navigate(`/interviews/${d.id}`)
    },
  })
  const tailor = useMutation({
    mutationFn: api.retailor,
    onSuccess: (d) => navigate(`/interviews/${d.id}`),
  })
  const remove = useMutation({
    mutationFn: api.deleteProject,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      qc.invalidateQueries({ queryKey: ['interviews'] })
    },
  })
  const discard = useMutation({
    mutationFn: api.deleteInterview,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['interviews'] }),
  })

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Start a new application</CardTitle>
          <CardDescription>
            A short interview about what you're building tailors the steps, and the knowledge-base resources behind
            them, to your project.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (name.trim()) start.mutate()
            }}
          >
            <Input
              placeholder="Project name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="project-name"
            />
            <Button type="submit" disabled={!name.trim() || start.isPending} data-testid="start-interview">
              Start interview
            </Button>
          </form>
        </CardContent>
      </Card>

      {editing && (
        <div className="flex justify-end">
          <InterviewQuestionsDialog />
        </div>
      )}

      {drafts.data && drafts.data.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground">Unfinished interviews</h2>
          <ul className="flex flex-col gap-2" data-testid="draft-list">
            {drafts.data.map((d) => (
              <li key={d.id} className="flex items-center gap-2 rounded-lg border border-dashed px-4 py-3">
                <Link to={`/interviews/${d.id}`} className="font-medium hover:underline" data-testid="resume-interview">
                  {d.project_id === null ? d.name : `Re-tailor: ${d.name}`}
                </Link>
                <span className="text-xs text-muted-foreground">resume interview</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="ml-auto"
                  aria-label={`Discard interview ${d.name}`}
                  onClick={() => {
                    if (confirm(`Discard the interview for "${d.name}"?`)) discard.mutate(d.id)
                  }}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <ul className="flex flex-col gap-2" data-testid="project-list">
        {projects.data?.map((p) => (
          <li key={p.id} className="flex items-center gap-2 rounded-lg border px-4 py-3">
            <Link to={`/projects/${p.id}`} className="font-medium hover:underline">
              {p.name}
            </Link>
            {p.tailored ? (
              <Badge variant="secondary">tailored</Badge>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => tailor.mutate(p.id)} data-testid={`tailor-${p.id}`}>
                <Wand2 /> Tailor
              </Button>
            )}
            <span className="text-xs text-muted-foreground">
              created {new Date(p.created_at * 1000).toLocaleDateString()}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="ml-auto"
              aria-label={`Delete ${p.name}`}
              onClick={() => {
                if (confirm(`Delete project "${p.name}" and its progress?`)) remove.mutate(p.id)
              }}
            >
              <Trash2 />
            </Button>
          </li>
        ))}
        {projects.data?.length === 0 && <p className="text-sm text-muted-foreground">No projects yet.</p>}
      </ul>
    </div>
  )
}
