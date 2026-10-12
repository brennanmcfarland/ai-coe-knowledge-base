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
  facets: Record<string, string[]>
  /** False for steps written from general knowledge ("Not from KB"). */
  grounded: boolean
  /** global: from the shared ontology; project: generated for this project. */
  scope: Scope
  /** A global step that started matching this project after it was tailored. */
  is_new: boolean
  promoted_to: string | null
}

export type Scope = 'global' | 'project' | 'bridge'

export interface GraphEdge {
  source: string
  target: string
  status: Status
  locked: boolean
  scope: Scope
}

export type ProposalKind = 'include' | 'exclude' | 'add_node' | 'remove_node' | 'add_edge' | 'new'

export interface ProjectProposal {
  key: string
  kind: ProposalKind
  title: string
  node_id: string | null
  source: string | null
  target: string | null
  grounded: boolean
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  hash: string
  proposals: number
  tailored: boolean
  overlay_hash: string | null
  project_proposals: ProjectProposal[]
}

export interface Project {
  id: number
  name: string
  created_at: number
  tailored: boolean
}

export type JobState = 'idle' | 'running' | 'succeeded' | 'failed'

export interface Job {
  state: JobState
  messages: string[]
  next: number
  error: string | null
  result: { project_id?: number; stage?: string } | null
}

export type Answer = string | string[]

export interface FollowUp {
  id: string
  section: string
  text: string
  kind: QuestionKind
  options: string[]
  answer: Answer | null
}

export interface Draft {
  id: string
  name: string
  project_id: number | null
  created_at: number
  section: number
  stage: 'questions' | 'followups'
  answers: Record<string, Answer>
  followups: FollowUp[]
  job: Job | null
}

export type QuestionKind = 'single' | 'multi' | 'text'

export interface Option {
  value: string
  label: string
  status: Status
  locked: boolean
}

export interface Question {
  id: string
  section: string
  text: string
  kind: QuestionKind
  facet: boolean
  options: Option[]
  status: Status
}

export interface InterviewDef {
  sections: { id: string; title: string; max_followups: number }[]
  questions: Question[]
  hash: string
  proposals: number
}

export interface LlmStatus {
  state: 'missing' | 'downloading' | 'stopped' | 'starting' | 'ready' | 'failed'
  error: string | null
  download: { file: string; done: number; total: number } | null
  approx_bytes: number
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
  renameProject: (id: number, name: string) => request<Project>('PATCH', `/projects/${id}`, { name }),
  deleteProject: (id: number) => request<void>('DELETE', `/projects/${id}`),
  retailor: (id: number) => request<Draft>('POST', `/projects/${id}/retailor`),

  llmStatus: () => request<LlmStatus>('GET', '/llm/status'),
  llmDownload: () => request<LlmStatus>('POST', '/llm/download'),
  llmCancelDownload: () => request<LlmStatus>('POST', '/llm/download/cancel'),
  llmWarm: () => request<LlmStatus>('POST', '/llm/warm'),

  interviews: () => request<Draft[]>('GET', '/interviews'),
  createInterview: (name: string) => request<Draft>('POST', '/interviews', { name }),
  interview: (id: string) => request<Draft>('GET', `/interviews/${enc(id)}`),
  renameInterview: (id: string, name: string) => request<Draft>('PATCH', `/interviews/${enc(id)}`, { name }),
  deleteInterview: (id: string) => request<void>('DELETE', `/interviews/${enc(id)}`),
  answer: (id: string, questionId: string, value: Answer | null) =>
    request<Draft>('PUT', `/interviews/${enc(id)}/answers/${enc(questionId)}`, { value }),
  advance: (id: string) =>
    request<{ job_started: boolean; draft: Draft | null }>('POST', `/interviews/${enc(id)}/advance`),
  interviewJob: (id: string, since = 0) => request<Job>('GET', `/interviews/${enc(id)}/job?since=${since}`),

  interviewDef: (edit: boolean) => request<InterviewDef>('GET', `/interview-def${edit ? '?edit=true' : ''}`),

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

/** Edits to a tailored project's own steps, edges and proposals; each needs the overlay hash. */
export const projectEdits = (pid: number) => ({
  renameNode: (h: string, id: string, title: string) =>
    request<EditResult>('PATCH', `/projects/${pid}/overlay/nodes/${enc(id)}`, { title }, h),
  deleteNode: (h: string, id: string) =>
    request<EditResult>('DELETE', `/projects/${pid}/overlay/nodes/${enc(id)}`, undefined, h),
  setChecklist: (h: string, id: string, items: { id: string | null; text: string }[]) =>
    request<EditResult>('PUT', `/projects/${pid}/overlay/nodes/${enc(id)}/checklist`, { items }, h),
  setCitations: (h: string, id: string, items: { chunk_id: string; n: number | null }[]) =>
    request<EditResult>('PUT', `/projects/${pid}/overlay/nodes/${enc(id)}/citations`, { items }, h),
  addEdge: (h: string, source: string, target: string) =>
    request<EditResult>('POST', `/projects/${pid}/overlay/edges`, { source, target }, h),
  deleteEdge: (h: string, source: string, target: string) =>
    request<EditResult>('DELETE', `/projects/${pid}/overlay/edges/${enc(source)}/${enc(target)}`, undefined, h),
  resolveProposal: (h: string, key: string, accept: boolean) =>
    request<EditResult>('POST', `/projects/${pid}/proposals/${enc(key)}/resolve`, { accept }, h),
  promote: (h: string, id: string) =>
    request<EditResult>('POST', `/projects/${pid}/nodes/${enc(id)}/promote`, undefined, h),
})

export type NodeOps = Pick<typeof edits, 'renameNode' | 'deleteNode' | 'setChecklist' | 'setCitations'>

/** Where edits to a node go: the project's overlay for its own steps, else the global ontology. */
export function nodeOps(pid: number, node: GraphNode): NodeOps {
  return node.scope === 'project' ? projectEdits(pid) : edits
}

/** Curation of proposed interview questions and options; each needs the interview.yaml hash. */
export const interviewEdits = {
  resolveQuestion: (h: string, qid: string, accept: boolean) =>
    request<EditResult>('POST', `/interview-def/questions/${enc(qid)}/resolve`, { accept }, h),
  resolveOption: (h: string, qid: string, value: string, accept: boolean) =>
    request<EditResult>(
      'POST',
      `/interview-def/questions/${enc(qid)}/options/${enc(value)}/resolve`,
      { accept },
      h,
    ),
}
