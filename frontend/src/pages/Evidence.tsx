import { useQuery } from '@tanstack/react-query'
import { IS_PRINT } from '../print'
import { createContext, useContext, useState, type ReactNode } from 'react'
import { analysisApi, type EvidenceItem } from '../apiAnalysis'
import { Badge, Modal, Spinner, fmtDate } from '../ui'

const KIND: Record<string, { label: string; tone: 'green' | 'violet' | 'blue' }> = {
  fact: { label: '공시·시세 확인 사실', tone: 'green' },
  calc: { label: '공식 데이터 기반 계산', tone: 'blue' },
  estimate: { label: '추정(명시적 가정)', tone: 'violet' },
  news: { label: '언론 보도(보조 근거)', tone: 'violet' },
}

type Ctx = { open: (ids: string[], title?: string) => void }
const EvidenceCtx = createContext<Ctx>({ open: () => {} })
export const useEvidence = () => useContext(EvidenceCtx)

export function EvidenceProvider({ projectId, runId, children }: { projectId: string; runId: string; children: ReactNode }) {
  const [state, setState] = useState<{ ids: string[]; title?: string } | null>(null)
  return (
    <EvidenceCtx.Provider value={{ open: (ids, title) => setState({ ids, title }) }}>
      {children}
      {state && <EvidenceModal projectId={projectId} runId={runId} ids={state.ids} title={state.title} onClose={() => setState(null)} />}
    </EvidenceCtx.Provider>
  )
}

export function EvidenceLink({ ids, children, title }: { ids: (string | null | undefined)[]; children?: ReactNode; title?: string }) {
  const { open } = useEvidence()
  const valid = ids.filter(Boolean) as string[]
  if (IS_PRINT) return <>{children ?? valid.join(', ')}</>
  if (!valid.length) return <>{children}</>
  return (
    <button onClick={() => open(valid, title)} className="inline-flex items-center gap-0.5 text-blue-600 hover:underline dark:text-blue-400" title="분석 근거 상세보기">
      {children ?? valid.join(', ')}
      <span className="text-[10px]">ⓘ</span>
    </button>
  )
}

function EvidenceModal({ projectId, runId, ids, title, onClose }: { projectId: string; runId: string; ids: string[]; title?: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ['evidence', runId, ids.join(',')], queryFn: () => analysisApi.evidence(projectId, runId, ids) })
  return (
    <Modal open onClose={onClose} title={title ?? '분석 근거 상세보기'} wide>
      {q.isLoading && <Spinner />}
      <div className="space-y-3">
        {q.data?.map((e) => <EvidenceCard key={e.eid} e={e} />)}
      </div>
    </Modal>
  )
}

export function EvidenceCard({ e }: { e: EvidenceItem }) {
  const k = KIND[e.kind] ?? KIND.calc
  const detail = e.detail ?? {}
  const quote = (detail as { quote?: string }).quote
  const rcepts = (detail as { rcept_nos?: string[] }).rcept_nos
  return (
    <div className="rounded-md border border-slate-200 p-3 text-sm dark:border-slate-700">
      <div className="flex flex-wrap items-center gap-2">
        <span className="tabular rounded bg-slate-100 px-1.5 text-[11px] font-semibold dark:bg-slate-800">{e.eid}</span>
        <span className="font-semibold">{e.label}</span>
        <Badge tone={k.tone}>{k.label}</Badge>
      </div>
      <div className="tabular mt-1 text-lg font-bold">{e.display ?? '데이터 없음'}</div>
      <dl className="mt-2 grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 text-xs">
        {e.company_name && <><dt className="text-slate-500">기업</dt><dd>{e.company_name}</dd></>}
        {e.period && <><dt className="text-slate-500">회계기간</dt><dd>{e.period}</dd></>}
        {e.as_of && <><dt className="text-slate-500">기준일</dt><dd>{e.as_of}</dd></>}
        {e.method && <><dt className="text-slate-500">계산 방법</dt><dd className="whitespace-pre-line">{e.method}</dd></>}
        {e.source && <><dt className="text-slate-500">출처</dt><dd>{e.source}</dd></>}
        {e.rcept_no && <><dt className="text-slate-500">공시 접수번호</dt><dd className="tabular">{e.rcept_no} ({fmtDate(e.rcept_no.slice(0, 8))})</dd></>}
        {rcepts && rcepts.length > 1 && <><dt className="text-slate-500">사용 보고서</dt><dd className="tabular">{rcepts.map((r) => <a key={r} href={`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${r}`} target="_blank" rel="noreferrer" className="mr-2 text-blue-600 hover:underline">{r}</a>)}</dd></>}
        {e.url && <><dt className="text-slate-500">원문 URL</dt><dd><a href={e.url} target="_blank" rel="noreferrer" className="break-all text-blue-600 hover:underline">{e.url}</a></dd></>}
        {e.impact && <><dt className="text-slate-500">평가에 미친 영향</dt><dd>{e.impact}</dd></>}
      </dl>
      {quote && <div className="mt-2 border-l-2 border-slate-300 pl-2 text-xs text-slate-600 dark:text-slate-400">원문: “{quote}”</div>}
    </div>
  )
}
