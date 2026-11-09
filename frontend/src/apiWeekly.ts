// 주간 주가 분석 API (통합 분석과 별도 분기)
const json = async <T,>(res: Response): Promise<T> => {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const b = await res.json()
      msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
    } catch {
      /* ignore */
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export interface WeeklySummary {
  target_return: number; peer_return: number | null; market_return: number; excess_vs_peers: number | null; excess_vs_market: number
  residual_sum: number; z_week: number | null; pattern: 'comove' | 'idiosyncratic' | 'mixed'; pattern_label: string
  same_dir_days: number; judged_days: number; abnormal_days: string[]; volatility_flags: string[]; volatility_label: string
  eids?: Record<string, string>
}
export interface WeeklyDay {
  date: string; weekday: string; ret: number; market: number; peer_avg: number | null; expected: number; market_part: number; industry_part: number
  residual: number; z: number | null; abnormal: boolean; same_dir: boolean | null; range?: number | null; volume_ratio?: number | null; close?: number
  disclosures?: { date: string; title: string; url: string; company: string }[]
  news?: { event: string; date: string; url: string; outlets: string[]; sentiment: string; verification?: string }[]
  explanation?: string; eid?: string
}
export interface WeeklyPeer {
  company_id: number; name: string; available: boolean; weight: number; use_type?: string; category?: string
  week_return?: number; daily?: (number | null)[]; same_dir_days?: number; corr_60d?: number | null
}
export interface WeeklyResult {
  week: { start: string; end: string; trading_days: number; base_date: string; base_close: number | null; last_close: number | null }
  summary: WeeklySummary
  days: WeeklyDay[]
  peers: WeeklyPeer[]
  volatility: Record<string, number | null>
  model: { alpha_daily: number; beta_market: number; beta_industry: number | null; r2: number; sigma_daily: number; n: number; period: string[]; market_label: string }
  chart: { target: [string, number][]; market: [string, number][]; peer_avg: [string, number][] | null; peers: Record<string, [string, number][]> }
  thresholds: Record<string, number>
  disclosures: { date: string; company_id: number; company: string; title: string; url: string; rcept_no: string }[]
  news: { date: string; date_certain: boolean; company_id: number | null; company: string; event: string; url: string; outlets: string[]; articles: number; sentiment: string; verification?: string; summary?: string }[]
  news_enabled: boolean
  peer_list: { company_id: number; name: string; weight: number }[]
}
export interface WeeklyReport {
  status: string; model: string | null; headline: string; verification: string; errors: string[]
  sections: { key: string; title: string; paragraphs: { ref: string; text: string; evidence_ids: string[] }[] }[]
  held: { ref: string; section: string; text: string; issues: string[] }[]
  evidence: { eid: string; kind: string; label: string; display: string | null; method: string | null; source: string | null; url: string | null }[]
}
export interface WeeklyRun {
  id: string; project_id: string; week_start: string; week_end: string; status: 'queued' | 'running' | 'succeeded' | 'failed'
  stage: string | null; peer_version_no: number; started_at: string; finished_at: string | null
  summary?: WeeklySummary | null; headline?: string | null
  messages?: { level: string; message: string }[]; result?: WeeklyResult; report?: WeeklyReport; options?: { include_news?: boolean }
}

export const weeklyApi = {
  range: (pid: string) => fetch(`/api/projects/${pid}/weekly/range`).then((r) => json<{ earliest: string; latest: string; today: string }>(r)),
  list: (pid: string) => fetch(`/api/projects/${pid}/weekly`).then((r) => json<WeeklyRun[]>(r)),
  get: (pid: string, id: string) => fetch(`/api/projects/${pid}/weekly/${id}`).then((r) => json<WeeklyRun>(r)),
  start: (pid: string, weekStart: string, includeNews: boolean) =>
    fetch(`/api/projects/${pid}/weekly`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ week_start: weekStart, include_news: includeNews }),
    }).then((r) => json<WeeklyRun>(r)),
}

// ---- 주(월~일) 날짜 계산 — 로컬 날짜 문자열로 처리(시간대 영향 없음)
export function toISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
export function parseISO(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
export function mondayOf(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7))
  return x
}
export function addDays(s: string, n: number): string {
  const d = parseISO(s)
  d.setDate(d.getDate() + n)
  return toISO(d)
}
/** 월요일 → ISO 주차 문자열(YYYY-Www, <input type="week"> 값) */
export function isoWeekOf(monday: string): string {
  const d = parseISO(monday)
  const th = new Date(d)
  th.setDate(d.getDate() + 3)
  const jan4 = new Date(th.getFullYear(), 0, 4)
  const wk = 1 + Math.round((th.getTime() - mondayOf(jan4).getTime()) / 86400000 / 7 - 3 / 7)
  return `${th.getFullYear()}-W${String(wk).padStart(2, '0')}`
}
/** ISO 주차 문자열 → 그 주 월요일 */
export function mondayOfIsoWeek(v: string): string | null {
  const m = /^(\d{4})-W(\d{2})$/.exec(v)
  if (!m) return null
  const mon = mondayOf(new Date(Number(m[1]), 0, 4))
  mon.setDate(mon.getDate() + (Number(m[2]) - 1) * 7)
  return toISO(mon)
}
