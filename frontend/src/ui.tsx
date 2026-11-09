import type { ReactNode } from 'react'
import type { Category, UseType } from './api'

export const CATEGORY_LABELS: Record<Category, string> = {
  A: '직접 경쟁기업',
  B: '유관 장비·부품기업',
  C: '전방산업·주요 고객기업',
  D: '소재·기술 관련 기업',
  E: '기타 관련 기업',
}
export const USE_TYPE_LABELS: Record<UseType, string> = {
  valuation: '적정가치 비교용',
  industry_reference: '산업 참고용',
  tech_competition: '기술 경쟁용',
}
export const MARKET_LABELS: Record<string, string> = {
  KOSPI: '코스피',
  KOSDAQ: '코스닥',
  KONEX: '코넥스',
  OTHER: '기타법인',
  UNLISTED: '비상장',
  FOREIGN: '해외',
  UNKNOWN: '확인 불가',
}
export const ORIGIN_LABELS: Record<string, string> = {
  seed: '초기 후보',
  report_mention: '검색 발견',
  industry: '업종코드 탐색',
  customer: '고객사 중복 계약',
  ai: 'AI 추천',
  user: '사용자 추가',
}
export const VERIFICATION_LABELS: Record<string, { label: string; tone: Tone }> = {
  verified: { label: 'DART 검증', tone: 'green' },
  mismatch: { label: '정보 불일치', tone: 'amber' },
  not_found: { label: 'DART 미확인', tone: 'red' },
  unverified: { label: '미검증', tone: 'slate' },
}
export const STEPS = [
  { no: 1, path: 'peers', label: 'STEP 1', title: '동종업계 탐색·확정' },
  { no: 2, path: 'analysis', label: 'STEP 2', title: '데이터 수집·분석' },
  { no: 3, path: 'results', label: 'STEP 3', title: '분석 결과' },
  { no: 4, path: 'history', label: 'STEP 4', title: '이력·재분석' },
]

export function fmtDateTime(s: string | null | undefined): string {
  if (!s) return '—'
  const d = new Date(s)
  return d.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  if (/^\d{8}$/.test(s)) return `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6)}`
  return new Date(s).toLocaleDateString('ko-KR')
}

export type Tone = 'slate' | 'blue' | 'green' | 'amber' | 'red' | 'violet'
const TONES: Record<Tone, string> = {
  slate: 'bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700',
  blue: 'bg-blue-50 text-blue-700 ring-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:ring-blue-900',
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-900',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-900',
  red: 'bg-red-50 text-red-700 ring-red-200 dark:bg-red-950 dark:text-red-300 dark:ring-red-900',
  violet: 'bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-950 dark:text-violet-300 dark:ring-violet-900',
}

export function Badge({ tone = 'slate', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}>
      {children}
    </div>
  )
}

type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
const BTN: Record<BtnVariant, string> = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-300 dark:disabled:bg-blue-900',
  secondary:
    'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800',
  ghost: 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
  danger: 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950',
}
export function Button({
  variant = 'secondary',
  size = 'md',
  className = '',
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: 'sm' | 'md' }) {
  const sz = size === 'sm' ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-sm'
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${sz} ${BTN[variant]} ${className}`}
    />
  )
}

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return <span className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${className}`} aria-label="로딩 중" />
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 px-6 py-12 text-center dark:border-slate-700">
      <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title}</div>
      {children && <div className="max-w-xl text-sm text-slate-500 dark:text-slate-400">{children}</div>}
    </div>
  )
}

export function Notice({ tone = 'amber', children }: { tone?: 'amber' | 'red' | 'blue' | 'slate'; children: ReactNode }) {
  const c = {
    amber: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200',
    red: 'border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-200',
    blue: 'border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-200',
    slate: 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300',
  }[tone]
  return <div className={`rounded-md border px-3 py-2 text-sm ${c}`}>{children}</div>
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/50 p-6 pt-20" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label={title}
        className={`w-full ${wide ? 'max-w-4xl' : 'max-w-lg'} rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="닫기">✕</button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

export function ScoreBar({ score, coverage }: { score: number | null; coverage: number | null }) {
  if (score === null) {
    return (
      <div className="text-xs text-slate-500" title="근거가 있는 평가 항목이 50% 미만이라 점수를 산출하지 않았습니다.">
        평가 보류 <span className="tabular">({Math.round((coverage ?? 0) * 100)}% 평가)</span>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-2" title={`평가 커버리지 ${Math.round((coverage ?? 0) * 100)}%`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
        <div className="h-full bg-blue-600" style={{ width: `${score}%` }} />
      </div>
      <span className="tabular text-sm font-semibold">{score.toFixed(1)}</span>
    </div>
  )
}
