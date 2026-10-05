import { useQuery } from '@tanstack/react-query'
import { Link, Outlet } from 'react-router'
import { api } from '@/api'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useEditMode } from '@/lib/editMode'
import { SyncIndicator } from './SyncIndicator'

export function Layout() {
  const { editing, setEditing } = useEditMode()
  const auth = useQuery({ queryKey: ['auth'], queryFn: api.authStatus })
  const onto = useQuery({ queryKey: ['ontology-status'], queryFn: api.ontologyStatus })

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex flex-wrap items-center gap-4 border-b px-4 py-2">
        <Link to="/projects" className="font-semibold">
          CoE Build Wizard
        </Link>
        <div className="ml-auto flex flex-wrap items-center gap-4">
          <SyncIndicator />
          <div className="flex items-center gap-2">
            <Switch id="edit-mode" checked={editing} onCheckedChange={setEditing} data-testid="edit-toggle" />
            <Label htmlFor="edit-mode">Edit mode</Label>
          </div>
          <Link to="/login" className="text-sm text-muted-foreground hover:underline">
            {auth.data?.logged_in ? (auth.data.user?.username ?? 'Logged in') : 'Log in'}
          </Link>
        </div>
      </header>
      {onto.data && !onto.data.valid && (
        <Alert variant="destructive" className="m-4 w-auto" data-testid="ontology-error">
          <AlertTitle>The ontology file is invalid</AlertTitle>
          <AlertDescription className="whitespace-pre-wrap">{onto.data.error}</AlertDescription>
        </Alert>
      )}
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}
