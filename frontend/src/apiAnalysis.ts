// PHASE 2-4 API types and client
import { ApiError } from './api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } })
  if (!res.ok) {
    let detail: unknown = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.detail ?? body
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}
const get = <T>(p: string) => request<T>(p)

export type Scenario = 'conservative' | 'base' | 'optimistic'
export type ScenarioMap = Partial<Record<Scenario, number | null>>

export interface StepStatus { status: 'running' | 'done' | 'failed' | 'skipped' | 'partial'; detail: string | null }
export interface RunProgress {
  stage?: string
  stage_detail?: string | null
  companies?: Record<string, { company_id: number; name: string; role: string; category?: string; steps: Record<string, StepStatus> }>
}
export interface RunSummary {
  verdict?: string
  verdict_reason?: string | null
  fair_value?: ScenarioMap | null
  confidence?: number
  price?: number | null
  price_date?: string | null
  market_cap?: number | null
  upside_base?: number | null
  report_summary?: string | null
  fair_value_eid?: string | null
  valuation_peers?: string[]
}
export interface AnalysisRun {
  id: string
  project_id: string
  status: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed'
  started_at: string
  finished_at: string | null
  peer_version_no: number
  price_as_of: string | null
  model_version: string
  ai_provider: string | null
  ai_model: string | null
  summary: RunSummary
  progress: RunProgress
  messages?: { level: string; message: string; at?: string }[]
  assumptions?: Record<string, string | Record<string, number>>
  data_versions?: Record<string, unknown>
}
export interface MethodInput {
  multiple: string
  key: string
  fallback?: boolean
  peers?: { name: string; value: number; eid?: string }[]
  quantiles: Record<Scenario, number>
  n?: number
  range?: number[]
}
export interface MethodResult {
  method: string
  label: string
  available: boolean
  notes: string[]
  scenarios: Record<string, { per_share: number | null; forward_revenue?: number; forward_ebit?: number; multiple?: number }>
  inputs?: MethodInput[]
  assumptions?: { backlog?: number; backlog_source?: string; backlog_as_of?: string; conversion?: Record<Scenario, number>; op_margin?: Record<Scenario, number>; op_margin_history?: Record<string, number>; multiple_source?: string; multiples?: Record<Scenario, number>; undisclosed_contracts?: number }
  eid?: string
  peer_count?: number
  fallback?: boolean
  observations?: Record<string, { date: string; value: number }[]>
}
export interface Attribution {
  available: boolean
  reason?: string
  n?: number
  period?: string[]
  market_label?: string
  model?: { beta_market: number; t_market: number; beta_industry: number | null; t_industry: number | null; r2: number; method: string }
  windows?: Record<string, { total: number; market: number; industry: number; individual: number; days: number }>
  abnormal_days?: { date: string; return: number; idiosyncratic: number; classification: string; disclosures: { rcept_no: string; report_nm: string; rcept_dt: string; url: string }[] }[]
  note?: string
}
export interface ContractRow {
  key: string; description: string | null; counterparty: string | null; contract_date: string | null; start_date: string | null; end_date: string | null
  amount: number | null; amount_disclosed: boolean; withheld_until: string | null; status: string; remaining_estimate: number | null
  filings: number; latest_rcept_no: string; latest_url: string; first_filed: string; sales_ratio: number | null
}
export interface QuarterRow { period: string; revenue: number | null; operating_income: number | null; net_income: number | null; op_margin: number | null; rcept_no: string }
export interface ValuationResult {
  company_id: number
  name: string
  stock_code: string | null
  market: string
  is_target: boolean
  category: string | null
  use_type: string | null
  verdict: string | null
  confidence: number | null
  fair_value: { scenarios?: ScenarioMap; envelope?: { low: number; high: number }; method_dispersion?: number; range_ratio?: number; weights?: Record<string, number>; verdict?: string; verdict_reason?: string | null; upside_base?: number; eid?: string; confidence_eid?: string }
  metrics: { values: Record<string, number | string | null>; na: Record<string, string>; eids: Record<string, string>; quarters: QuarterRow[]; annual: Record<string, Record<string, number>>; splits: unknown[] }
  methods?: Partial<Record<'forward' | 'peer' | 'history', MethodResult>>
  confidence_breakdown?: { points: number; reason: string }[]
  attribution?: Attribution
  data_status?: {
    backlog?: { amount: number | null; source_label: string; as_of: string; undisclosed_active: number; eid?: string; official?: { quote: string; check: string; url: string; title: string } } | null
    contracts?: { contracts: ContractRow[]; filings_total: number; contracts_total: number; duplicates_merged: number; active_count: number; undisclosed_active: number; backlog_estimate: number }
  }
  scenarios?: { method: string; scenario: Scenario; multiple_name: string | null; multiple: number | null; per_share: number | null }[]
}
export interface ReportParagraph {
  ref: string; text: string; evidence_ids: string[]; claim_type: 'fact' | 'estimate' | 'judgment'; status: 'verified' | 'unverified'
  issues: { type: string; severity: string; message: string; by: string }[]
}
export interface Report {
  status: string
  ai_model: string | null
  content: { sections: { key: string; title: string; paragraphs: ReportParagraph[]; missing?: boolean }[]; summary: string | null; final_verdict: string }
  review: { by_ai: boolean; model?: string; paragraphs: number; unverified: number; verdict_consistent: boolean; errors: string[]; overall_comment?: string; counter_evidence_reviewed?: boolean }
}
export interface Charts {
  companies?: { id: number; name: string; is_target: boolean; role: string }[]
  price_index?: Record<string, [string, number][]>
  market_index?: { label: string; points: [string, number][] }
  excess_return?: [string, number][]
  market_caps?: { id: number; name: string; value: number | null }[]
  multiples?: { id: number; name: string; per: number | null; pbr: number | null; psr: number | null; ev_ebit: number | null; ev_ebitda: number | null; na: Record<string, string | null> }[]
  scenarios?: Record<string, ScenarioMap | null>
  forward?: Record<Scenario, { forward_revenue: number; forward_ebit: number }>
  contracts_monthly?: [string, number][]
  history?: { run_id: string; date: string; price: number | null; verdict: string | null; conservative?: number; base?: number; optimistic?: number }[]
}
export interface Results { run: AnalysisRun; target: ValuationResult | null; companies: ValuationResult[]; report: Report | null; charts: Charts }
export interface EvidenceItem {
  eid: string; kind: 'fact' | 'estimate' | 'calc'; label: string; value: number | null; unit: string | null; display: string | null
  period: string | null; as_of: string | null; company_id: number | null; company_name: string | null; method: string | null
  source: string | null; url: string | null; rcept_no: string | null; impact: string | null; detail: Record<string, unknown>
}
export interface CompareResult {
  old: AnalysisRun
  new: AnalysisRun
  rows: { label: string; old: number | null; new: number | null; change: number | null }[]
  applied_multiples: Record<string, { old: number | null; new: number | null; multiple: string | null }>
  verdict: { old: string | null; new: string | null }
  peer_version: { old: number; new: number }
}

export const analysisApi = {
  start: (pid: string) => request<AnalysisRun>(`/api/projects/${pid}/analysis-runs`, { method: 'POST' }),
  runs: (pid: string) => get<AnalysisRun[]>(`/api/projects/${pid}/analysis-runs`),
  run: (pid: string, rid: string) => get<AnalysisRun>(`/api/projects/${pid}/analysis-runs/${rid}`),
  results: (pid: string, rid: string) => get<Results>(`/api/projects/${pid}/analysis-runs/${rid}/results`),
  company: (pid: string, rid: string, cid: number) =>
    get<{ valuation: ValuationResult; target: ValuationResult | null; company: Record<string, string | null> }>(`/api/projects/${pid}/analysis-runs/${rid}/companies/${cid}`),
  evidence: (pid: string, rid: string, ids?: string[]) =>
    get<EvidenceItem[]>(`/api/projects/${pid}/analysis-runs/${rid}/evidence${ids?.length ? `?ids=${ids.join(',')}` : ''}`),
  compare: (pid: string, a: string, b: string) => get<CompareResult>(`/api/projects/${pid}/analysis-runs/compare/${a}/${b}`),
  usage: () => get<Record<string, { used_today: number; limit: number }>>('/api/system/api-usage'),
}

// ------------------------------------------------------------ formatting
export function krw(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '데이터 없음'
  const a = Math.abs(v)
  if (a >= 1e12) return `${(v / 1e12).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}조원`
  if (a >= 1e8) return `${Math.round(v / 1e8).toLocaleString('ko-KR')}억원`
  return `${Math.round(v).toLocaleString('ko-KR')}원`
}
export function won(v: number | null | undefined): string {
  return v === null || v === undefined ? '데이터 없음' : `${Math.round(v).toLocaleString('ko-KR')}원`
}
export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? 'N/A' : `${(v * 100).toFixed(digits)}%`
}
export function mult(v: number | null | undefined): string {
  return v === null || v === undefined ? 'N/A' : `${v.toFixed(1)}배`
}
export const SCENARIO_LABEL: Record<Scenario, string> = { conservative: '보수적', base: '기준', optimistic: '낙관적' }
export const METHOD_LABEL: Record<string, string> = {
  forward: 'A. 미래이익(수주) 기반',
  peer: 'B. 동종업계 상대평가',
  history: 'C. 자사 과거 밸류에이션',
  combined: '가중 결합',
}
export const VERDICT_TONE: Record<string, 'green' | 'red' | 'blue' | 'slate'> = { 저평가: 'green', 고평가: 'red', 적정: 'blue', '판단 유보': 'slate' }
