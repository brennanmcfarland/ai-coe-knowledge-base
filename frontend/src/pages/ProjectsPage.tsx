import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { api } from '@/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

export function ProjectsPage() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const create = useMutation({
    mutationFn: () => api.createProject(name.trim()),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${p.id}`)
    },
  })
  const remove = useMutation({
    mutationFn: api.deleteProject,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Start a new application</CardTitle>
          <CardDescription>Each project tracks its own progress through the wizard.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (name.trim()) create.mutate()
            }}
          >
            <Input
              placeholder="Project name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="project-name"
            />
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              Create
            </Button>
          </form>
        </CardContent>
      </Card>

      <ul className="flex flex-col gap-2" data-testid="project-list">
        {projects.data?.map((p) => (
          <li key={p.id} className="flex items-center gap-2 rounded-lg border px-4 py-3">
            <Link to={`/projects/${p.id}`} className="font-medium hover:underline">
              {p.name}
            </Link>
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
