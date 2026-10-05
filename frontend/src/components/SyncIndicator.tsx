import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router'
import { api } from '@/api'
import { Button } from '@/components/ui/button'

function ago(ts: number | null): string {
  if (!ts) return 'never'
  const mins = Math.round((Date.now() / 1000 - ts) / 60)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`
  return `${Math.round(mins / 60 / 24)}d ago`
}

export function SyncIndicator() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const auth = useQuery({ queryKey: ['auth'], queryFn: api.authStatus })
  const sync = useQuery({
    queryKey: ['sync'],
    queryFn: () => api.syncStatus(),
    refetchInterval: (q) => (q.state.data?.state === 'running' ? 2000 : false),
  })
  const start = useMutation({
    mutationFn: api.startSync,
    onSettled: () => qc.invalidateQueries({ queryKey: ['sync'] }),
  })

  const running = sync.data?.state === 'running'
  const last = sync.data?.messages.at(-1)
  const label = running
    ? (last ?? 'Syncing…')
    : sync.data?.state === 'failed'
      ? `Sync failed: ${sync.data.error}`
      : `Synced ${ago(sync.data?.last_sync ?? null)}`

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground" data-testid="sync-indicator">
      <span className="max-w-64 truncate" title={label}>
        {label}
      </span>
      <Button
        size="sm"
        variant="outline"
        disabled={running || start.isPending}
        onClick={() => (auth.data?.logged_in ? start.mutate() : navigate('/login?next=sync'))}
      >
        <RefreshCw className={running ? 'animate-spin' : undefined} />
        Sync from ClickUp
      </Button>
    </div>
  )
}
