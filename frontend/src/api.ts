// Backend API client. API keys never reach the browser — only the local FastAPI backend holds them.

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : ((detail as { message?: string })?.message ?? `HTTP ${status}`))
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail: unknown = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.detail ?? body
      if (Array.isArray(detail)) detail = detail.map((d: { msg?: string }) => d.msg).join(', ')
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const get = <T>(p: string) => request<T>(p)
const send = <T>(method: string, p: string, body?: unknown) =>
  request<T>(p, { method, body: body === undefined ? undefined : JSON.stringify(body) })

export type Category = 'A' | 'B' | 'C' | 'D' | 'E'
export type UseType = 'valuation' | 'industry_reference' | 'tech_competition'
export type MemberStatus = 'proposed' | 'included' | 'excluded'

export interface Company {
  id: number
  name: string
  name_en: string | null
  corp_code: string | null
  stock_code: string | null
  market: string
  is_listed: boolean | null
  country: string
  induty_code: string | null
  ceo: string | null
  homepage: string | null
  is_verified: boolean
  verification_note: string | null
  info_source: string | null
  info_fetched_at: string | null
}

export interface PeerState {
  latest_version_no: number
  draft_dirty: boolean
  included_count: number
  last_search: { id: number; status: string; started_at: string; finished_at: string | null } | null
}

export interface Project {
  id: string
  title: string
  current_step: number
  step_label: string
  created_at: string
  updated_at: string
  last_analysis_at: string | null
  last_analysis_status: string | null
  company: Company
  peer_state: PeerState
}

export interface SourceRef {
  type: string
  title?: string
  url?: string
  rcept_no?: string
  filed_date?: string
  section?: string | null
  note?: string | null
  quote?: string
}

export interface ScoreItem {
  key: string
  label: string
  weight: number
  level: number | null
  level_label: string
  rationale: string
  origin: string
  evidence: string[] | null
}

export interface Candidate {
  id: number
  session_id: number
  category: Category
  use_type: UseType
  origin: string
  main_business: string | null
  main_products: string | null
  similarity: string | null
  selection_reason: string | null
  caution: string | null
  score: number | null
  score_coverage: number | null
  score_breakdown: ScoreItem[]
  sources: SourceRef[]
  verification_status: string
  verification_note: string | null
}

export interface Member {
  id: number
  category: Category
  use_type: UseType
  status: MemberStatus
  added_by: string
  note: string | null
  updated_at: string
  company: Company
  candidate: Candidate | null
}

export interface VersionSummary {
  id: number
  version_no: number
  label: string
  note: string | null
  confirmed_at: string
  member_count: number
  members?: { company_id: number; category: Category; use_type: UseType; score: number | null; snapshot: { company: { name: string; stock_code: string | null; market: string } } }[]
}

export interface PeerGroup {
  group_id: number
  latest_version_no: number
  draft_dirty: boolean
  updated_at: string
  members: Member[]
  versions: VersionSummary[]
}

export interface SearchSession {
  id: number
  status: 'running' | 'succeeded' | 'partial' | 'failed'
  started_at: string
  finished_at: string | null
  rubric_version: string
  ai_provider: string | null
  ai_model: string | null
  messages: { level: 'info' | 'warning' | 'error'; message: string }[]
  sources_used: (SourceRef & { summary?: Record<string, string> })[]
  candidate_count: number
}

export interface SystemStatus {
  integrations: { database: boolean; dart: boolean; krx: boolean; news: boolean; ai_provider: string; ai_ready: boolean }
  database_ok: boolean
  corp_registry_count: number
  labels: { categories: Record<Category, string>; use_types: Record<UseType, string>; steps: Record<string, string> }
}

export interface CompanyHit {
  corp_code: string
  corp_name: string
  corp_eng_name: string | null
  stock_code: string | null
  is_listed_hint: boolean
  existing_project_id: string | null
}

export interface Rubric {
  version: string
  min_coverage: number
  criteria: { key: string; label: string; weight: number; description: string }[]
  note: string
}

export const api = {
  status: () => get<SystemStatus>('/api/system/status'),
  rubric: () => get<Rubric>('/api/peers/rubric'),
  searchCompanies: (q: string, listedOnly = false) =>
    get<CompanyHit[]>(`/api/companies/search?q=${encodeURIComponent(q)}&listed_only=${listedOnly}`),
  projects: (q?: string) => get<Project[]>(`/api/projects${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  project: (id: string) => get<Project>(`/api/projects/${id}`),
  createProject: (body: { corp_code?: string; manual?: { name: string; stock_code?: string; market?: string } }) =>
    send<Project>('POST', '/api/projects', body),
  patchProject: (id: string, body: { current_step?: number }) => send<Project>('PATCH', `/api/projects/${id}`, body),
  startPeerSearch: (id: string) => send<{ status: string }>('POST', `/api/projects/${id}/peer-search`),
  searchSessions: (id: string) => get<SearchSession[]>(`/api/projects/${id}/peer-search/sessions`),
  peers: (id: string) => get<PeerGroup>(`/api/projects/${id}/peers`),
  addPeer: (id: string, body: { corp_code?: string; manual_name?: string; manual_country?: string; category: Category; use_type?: UseType }) =>
    send<PeerGroup>('POST', `/api/projects/${id}/peers`, body),
  patchPeer: (id: string, mid: number, body: Partial<{ category: Category; use_type: UseType; status: MemberStatus; note: string }>) =>
    send<PeerGroup>('PATCH', `/api/projects/${id}/peers/${mid}`, body),
  bulkStatus: (id: string, ids: number[], status: MemberStatus) =>
    send<PeerGroup>('POST', `/api/projects/${id}/peers/bulk-status`, { membership_ids: ids, status }),
  confirmPeers: (id: string, note?: string) => send<VersionSummary>('POST', `/api/projects/${id}/peers/confirm`, { note }),
  version: (id: string, no: number) => get<VersionSummary>(`/api/projects/${id}/peers/versions/${no}`),
}
