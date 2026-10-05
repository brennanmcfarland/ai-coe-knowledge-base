import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { api } from '@/api'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

function SetupForm({ redirectUri }: { redirectUri: string }) {
  const qc = useQueryClient()
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const save = useMutation({
    mutationFn: () => api.authSetup(clientId, secret),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth'] }),
  })
  return (
    <form
      className="flex flex-col gap-4"
      data-testid="setup-form"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <p className="text-sm text-muted-foreground">
        Create an OAuth app in ClickUp (Settings → Integrations → ClickUp API) with redirect URL{' '}
        <code className="rounded bg-muted px-1">{redirectUri}</code>, then paste its credentials. They are stored in
        your OS keyring, never on disk in plaintext.
      </p>
      <div className="flex flex-col gap-2">
        <Label htmlFor="client-id">Client ID</Label>
        <Input id="client-id" value={clientId} onChange={(e) => setClientId(e.target.value)} required />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="client-secret">Client secret</Label>
        <Input
          id="client-secret"
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          required
          autoComplete="off"
        />
      </div>
      {save.error && (
        <Alert variant="destructive">
          <AlertDescription>{save.error.message}</AlertDescription>
        </Alert>
      )}
      <Button type="submit" disabled={save.isPending}>
        Save credentials
      </Button>
    </form>
  )
}

export function LoginPage() {
  const qc = useQueryClient()
  const [params] = useSearchParams()
  const auth = useQuery({ queryKey: ['auth'], queryFn: api.authStatus })
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth'] }),
  })
  const sync = useMutation({
    mutationFn: api.startSync,
    onSettled: () => qc.invalidateQueries({ queryKey: ['sync'] }),
  })
  const error = params.get('error')

  return (
    <div className="flex justify-center p-6">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>ClickUp login</CardTitle>
          <CardDescription>
            Logging in is only needed to sync documents. The wizard works offline from local data.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {error && (
            <Alert variant="destructive" data-testid="login-error">
              <AlertDescription>Login failed: {error}</AlertDescription>
            </Alert>
          )}
          {auth.data && !auth.data.configured && <SetupForm redirectUri={auth.data.redirect_uri} />}
          {auth.data?.configured && !auth.data.logged_in && (
            <Button asChild data-testid="login-button">
              <a href="/api/auth/login">Log in with ClickUp</a>
            </Button>
          )}
          {auth.data?.logged_in && (
            <div className="flex flex-col gap-3" data-testid="logged-in">
              <p>
                Logged in as <strong>{auth.data.user?.username ?? auth.data.user?.email ?? 'ClickUp user'}</strong>.
                The session lasts until the backend restarts.
              </p>
              <div className="flex gap-2">
                <Button onClick={() => sync.mutate()} disabled={sync.isPending}>
                  Sync now
                </Button>
                <Button variant="outline" onClick={() => logout.mutate()}>
                  Log out
                </Button>
                <Button variant="ghost" asChild>
                  <Link to="/projects">Back to projects</Link>
                </Button>
              </div>
              {sync.error && <p className="text-sm text-destructive">{sync.error.message}</p>}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
