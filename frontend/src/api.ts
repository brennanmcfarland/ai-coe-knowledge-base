// Typed client for the backend's /api routes.

export type WizardState = 'complete' | 'frontier' | 'locked'
export type Status = 'accepted' | 'proposed'

export interface ChecklistItem {
  id: string
  text: string
  checked: boolean
}

export interface Citation {
  n: number
  chunk_id: string
  excerpt: string
  url: string
  doc_title: string
  broken: boolean
}

export interface GraphNode {
  id: string
  title: string
  summary: string
  checklist: ChecklistItem[]
  citations: Citation[]
  status: Status
  locked_fields: string[]
  state: WizardState
  completed: boolean
  predecessors: string[]
  successors: string[]
}

export interface GraphEdge {
  source: string
  target: string
  status: Status
  locked: boolean
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  hash: string
  proposals: number
}

export interface Project {
  id: number
  name: string
  created_at: number
}

export interface AuthStatus {
  configured: boolean
  logged_in: boolean
  user: { username: string | null; email: string | null } | null
  redirect_uri: string
}

export interface SyncStatus {
  state: 'idle' | 'running' | 'succeeded' | 'failed'
  messages: string[]
  next: number
  error: string | null
  last_sync: number | null
}

export interface ChunkHit {
  id: string
  doc_title: string
  text: string
}

export class ApiError extends Error {
  status: number
  code?: string
  hash?: string

  constructor(status: number, message: string, code?: string, hash?: string) {
    super(message)
    this.status = status
    this.code = code
    this.hash = hash
  }
}

async function request<T>(method: string, path: string, body?: unknown, ifMatch?: string): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (ifMatch !== undefined) headers['If-Match'] = ifMatch
  const resp = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    let detail: unknown = resp.statusText
    try {
      detail = (await resp.json()).detail
    } catch {
      // non-JSON error body
    }
    if (detail && typeof detail === 'object') {
      const d = detail as { message?: string; code?: string; hash?: string }
      throw new ApiError(resp.status, d.message ?? JSON.stringify(d), d.code, d.hash)
    }
    throw new ApiError(resp.status, typeof detail === 'string' ? detail : 'Request failed')
  }
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

const enc = encodeURIComponent

export const api = {
  authStatus: () => request<AuthStatus>('GET', '/auth/status'),
  authSetup: (client_id: string, client_secret: string) =>
    request<{ configured: boolean }>('POST', '/auth/setup', { client_id, client_secret }),
  logout: () => request<void>('POST', '/auth/logout'),

  syncStatus: (since = 0) => request<SyncStatus>('GET', `/sync?since=${since}`),
  startSync: () => request<{ state: string }>('POST', '/sync'),

  projects: () => request<Project[]>('GET', '/projects'),
  project: (id: number) => request<Project>('GET', `/projects/${id}`),
  createProject: (name: string) => request<Project>('POST', '/projects', { name }),
  renameProject: (id: number, name: string) => request<Project>('PATCH', `/projects/${id}`, { name }),
  deleteProject: (id: number) => request<void>('DELETE', `/projects/${id}`),

  graph: (pid: number, edit: boolean) =>
    request<Graph>('GET', `/projects/${pid}/graph${edit ? '?edit=true' : ''}`),
  setCompleted: (pid: number, nodeId: string, completed: boolean) =>
    request<void>('PUT', `/projects/${pid}/nodes/${enc(nodeId)}/complete`, { completed }),
  setChecked: (pid: number, nodeId: string, itemId: string, checked: boolean) =>
    request<void>('PUT', `/projects/${pid}/nodes/${enc(nodeId)}/checklist/${enc(itemId)}`, { checked }),

  ontologyStatus: () =>
    request<{ exists: boolean; valid: boolean; error?: string; nodes?: number; chunks?: number }>(
      'GET',
      '/ontology/status',
    ),
  searchChunks: (q: string) => request<ChunkHit[]>('GET', `/chunks?q=${enc(q)}`),
}

export interface EditResult {
  hash: string
  id?: string
}

/** Ontology edits; each needs the hash the caller last saw. */
export const edits = {
  createNode: (h: string, title: string) => request<EditResult>('POST', '/ontology/nodes', { title }, h),
  renameNode: (h: string, id: string, title: string) =>
    request<EditResult>('PATCH', `/ontology/nodes/${enc(id)}`, { title }, h),
  deleteNode: (h: string, id: string) => request<EditResult>('DELETE', `/ontology/nodes/${enc(id)}`, undefined, h),
  mergeNode: (h: string, id: string, into: string) =>
    request<EditResult>('POST', `/ontology/nodes/${enc(id)}/merge`, { into }, h),
  setChecklist: (h: string, id: string, items: { id: string | null; text: string }[]) =>
    request<EditResult>('PUT', `/ontology/nodes/${enc(id)}/checklist`, { items }, h),
  setCitations: (h: string, id: string, items: { chunk_id: string; n: number | null }[]) =>
    request<EditResult>('PUT', `/ontology/nodes/${enc(id)}/citations`, { items }, h),
  resolveNode: (h: string, id: string, accept: boolean) =>
    request<EditResult>('POST', `/ontology/nodes/${enc(id)}/resolve`, { accept }, h),
  addEdge: (h: string, source: string, target: string) =>
    request<EditResult>('POST', '/ontology/edges', { source, target }, h),
  deleteEdge: (h: string, source: string, target: string) =>
    request<EditResult>('DELETE', `/ontology/edges/${enc(source)}/${enc(target)}`, undefined, h),
  resolveEdge: (h: string, source: string, target: string, accept: boolean) =>
    request<EditResult>('POST', `/ontology/edges/${enc(source)}/${enc(target)}/resolve`, { accept }, h),
}
