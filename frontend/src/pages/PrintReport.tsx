// 인쇄·PDF 전용 보고서 (/print/projects/:projectId/runs/:runId).
// 리서치 보고서 양식: 표지 요약 → 장별로 '분석 서술(근거 인용) → 그 근거가 되는 그림·표(번호·출처)' → 부록.
// 검증을 통과한 문단만 본문에 싣고, 미통과 문단은 부록 D에 사유와 함께 둔다.
//  - 각 장(chapter)은 새 페이지에서 시작, 블록(표·차트·문단 묶음)은 페이지 경계에서 쪼개지지 않음
//  - 화면 전용 요소(기간 탭, 툴팁 안내, 상세 링크)는 출력하지 않음
import { useQuery } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { analysisApi, krw, METHOD_LABEL, mult, pct, SCENARIO_LABEL, won, type Results, type ValuationResult } from '../apiAnalysis'
import {
  colorMap, ContractsChart, ExcessReturnChart, FairValueChart, ForwardChart, MarginChart, MarketCapChart, MultiplesChart,
  PriceCompareChart, QuarterlyChart,
} from '../charts'
import { CATEGORY_LABELS, fmtDate, fmtDateTime, USE_TYPE_LABELS } from '../ui'
import { EvidenceProvider } from './Evidence'
import { AttributionTable, EarningsAdjustment, IssueTimeline, MethodCard, ScheduleTable } from './Results'

const SC = ['conservative', 'base', 'optimistic'] as const

const ASSUMPTION_LABEL: Record<string, string> = {
  backlog_conversion: '수주잔고 매출 전환율', method_weights: '평가 방법 가중치', vendor_rule: '데이터 출처 규칙',
  market_index: '시장지수', price_adjustment: '수정주가 처리', fiscal_year: '회계 기준',
}
const NUM_LABEL: Record<string, string> = { conservative: '보수', base: '기준', optimistic: '낙관', peer: '상대평가', history: '자사 과거', forward: '미래이익' }
function fmtAssumption(v: unknown): string {
  if (typeof v === 'string') return v
  if (v && typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${NUM_LABEL[k] ?? k} ${typeof x === 'number' && x <= 1 ? pct(x, 0) : String(x)}`).join(' · ')
  }
  return String(v)
}

// ------------------------------------------------------------------ layout primitives
function Chapter({ no, title, sub, children, first, before }: { no: number | string; title: string; sub?: string; children: ReactNode; first?: boolean; before?: ReactNode }) {
  return (
    <section className={`pr-chapter ${first ? '' : 'pr-page-break'}`}>
      {before}
      <div className="mb-3 flex items-end justify-between border-b-2 border-slate-900 pb-1">
        <h2 className="text-[15px] font-bold"><span className="mr-2 text-slate-400">{no}</span>{title}</h2>
        {sub && <div className="text-[9px] text-slate-500">{sub}</div>}
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  )
}

function Block({ title, note, children, allowSplit }: { title?: string; note?: ReactNode; children: ReactNode; allowSplit?: boolean }) {
  return (
    <div className={allowSplit ? '' : 'pr-keep'}>
      {title && <h3 className="mb-1.5 border-l-[3px] border-slate-800 pl-2 text-[11.5px] font-semibold">{title}</h3>}
      {children}
      {note && <div className="mt-1 text-[9px] leading-snug text-slate-500">{note}</div>}
    </div>
  )
}

export function Th({ children, right }: { children?: ReactNode; right?: boolean }) {
  return <th className={`border-b border-slate-400 px-1.5 py-1 font-semibold text-slate-600 ${right ? 'text-right' : 'text-left'}`}>{children}</th>
}
export function Td({ children, right, strong, className = '' }: { children?: ReactNode; right?: boolean; strong?: boolean; className?: string }) {
  return <td className={`border-b border-slate-200 px-1.5 py-[3px] ${right ? 'tabular text-right' : ''} ${strong ? 'font-semibold' : ''} ${className}`}>{children}</td>
}

// ------------------------------------------------------------------ chapter contents
function SummaryBox({ r }: { r: Results }) {
  const s = r.run.summary
  const fv = r.target?.fair_value
  const verdict = s.verdict ?? '판단 유보'
  const tone = { 저평가: 'border-emerald-600 text-emerald-700', 고평가: 'border-red-600 text-red-700', 적정: 'border-blue-600 text-blue-700' }[verdict] ?? 'border-slate-500 text-slate-700'
  return (
    <div className="pr-keep grid grid-cols-[150px_1fr] gap-4">
      <div className={`flex flex-col items-center justify-center rounded border-2 p-3 ${tone}`}>
        <div className="text-[9px] text-slate-500">종합 판단</div>
        <div className="text-[26px] font-bold leading-tight">{verdict}</div>
        <div className="mt-1 text-[10px] text-slate-700">분석 신뢰도 <b>{s.confidence?.toFixed(0) ?? '—'}</b>/100</div>
      </div>
      <table className="w-full text-[10.5px]">
        <tbody>
          <tr><Td className="w-28 text-slate-500">현재 주가</Td><Td right strong>{won(s.price)}</Td><Td className="w-28 pl-4 text-slate-500">시가총액</Td><Td right>{krw(s.market_cap)}</Td></tr>
          <tr><Td className="text-slate-500">적정주가 (보수)</Td><Td right>{won(fv?.scenarios?.conservative)}</Td><Td className="pl-4 text-slate-500">적정주가 (낙관)</Td><Td right>{won(fv?.scenarios?.optimistic)}</Td></tr>
          <tr><Td className="text-slate-500">적정주가 (기준)</Td><Td right strong>{won(fv?.scenarios?.base)}</Td><Td className="pl-4 text-slate-500">현재가 대비 괴리율</Td><Td right strong>{pct(s.upside_base)}</Td></tr>
          <tr><Td className="text-slate-500">방법별 전체 범위</Td><Td right>{fv?.envelope ? `${won(fv.envelope.low)} ~ ${won(fv.envelope.high)}` : '—'}</Td><Td className="pl-4 text-slate-500">방법 간 기준값 차이</Td><Td right>{fv?.method_dispersion != null ? `${fv.method_dispersion.toFixed(1)}배` : '—'}</Td></tr>
        </tbody>
      </table>
      {s.verdict_reason && <div className="col-span-2 text-[10px] text-slate-600">판단 유보 사유: {s.verdict_reason}</div>}
    </div>
  )
}

export function MethodTable({ t }: { t: ValuationResult }) {
  const w = t.fair_value?.weights ?? {}
  return (
    <table className="w-full text-[10.5px]">
      <thead><tr><Th>평가 방법</Th><Th right>가중치</Th>{SC.map((k) => <Th key={k} right>{SCENARIO_LABEL[k]}</Th>)}<Th>상태</Th></tr></thead>
      <tbody>
        {(['forward', 'peer', 'history'] as const).map((k) => {
          const m = t.methods?.[k]
          return (
            <tr key={k}>
              <Td strong>{METHOD_LABEL[k]}</Td>
              <Td right>{w[k] != null ? pct(w[k], 0) : '—'}</Td>
              {SC.map((s) => <Td key={s} right strong={s === 'base'}>{m?.available ? won(m.scenarios[s]?.per_share) : '—'}</Td>)}
              <Td>{m?.available ? (m.fallback ? '적용(신뢰도 낮음)' : '적용') : '적용 불가'}</Td>
            </tr>
          )
        })}
        <tr className="bg-slate-50">
          <Td strong>가중 결합</Td><Td right>100%</Td>
          {SC.map((s) => <Td key={s} right strong>{won(t.fair_value?.scenarios?.[s])}</Td>)}<Td />
        </tr>
      </tbody>
    </table>
  )
}

export function PeerCompactTable({ r }: { r: Results }) {
  const rows = [...r.companies].sort((a, b) => Number(b.is_target) - Number(a.is_target))
  const v = (c: ValuationResult, k: string) => c.metrics.values?.[k] as number | null | undefined
  return (
    <table className="w-full text-[9.5px]">
      <thead>
        <tr><Th>기업</Th><Th>구분</Th><Th right>시가총액</Th><Th right>매출(TTM)</Th><Th right>OPM</Th><Th right>매출성장</Th><Th right>조정 PER</Th><Th right>EV/정상EBIT</Th><Th right>정상 P/E</Th><Th right>PBR</Th><Th right>ROE</Th><Th right>1Y</Th></tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.company_id} className={c.is_target ? 'bg-blue-50 font-semibold' : ''}>
            <Td>{c.name}{c.is_target && ' (고객사)'}</Td>
            <Td className="text-slate-500">{c.is_target ? '—' : `${c.category ?? ''}. ${(CATEGORY_LABELS[c.category as 'A'] ?? '').slice(0, 6)} · ${(USE_TYPE_LABELS[c.use_type as 'valuation'] ?? '').replace('용', '')}`}</Td>
            <Td right>{krw(v(c, 'market_cap'))}</Td><Td right>{krw(v(c, 'revenue_ttm'))}</Td><Td right>{pct(v(c, 'op_margin'))}</Td>
            <Td right>{pct(v(c, 'revenue_growth'))}</Td><Td right>{mult(v(c, 'per_adj'))}</Td><Td right>{mult(v(c, 'ev_ebit_norm'))}</Td>
            <Td right>{mult(v(c, 'pe_norm'))}</Td><Td right>{mult(v(c, 'pbr'))}</Td><Td right>{pct(v(c, 'roe'))}</Td><Td right>{pct(v(c, 'ret_1Y'))}</Td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ContractsCompact({ t }: { t: ValuationResult }) {
  const book = t.data_status?.contracts
  if (!book) return null
  const order: Record<string, number> = { active: 0, lapsed: 1, completed: 2, cancelled: 3 }
  const rows = [...book.contracts].sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || (b.contract_date ?? '').localeCompare(a.contract_date ?? '')).slice(0, 25)
  const st = (c: (typeof rows)[number]) => c.status === 'active' ? (c.end_undetermined ? '진행·납기 미정' : '진행') : c.status === 'completed' ? '이행완료' : c.status === 'lapsed' ? '기간 경과' : '해지'
  return (
    <>
      <div className="mb-1 text-[10px] text-slate-600">
        공시 {book.filings_total}건 → 계약 {book.contracts_total}건 (정정 {book.duplicates_merged}건 병합) · 진행 중 {book.active_count}건(금액 비공개 {book.undisclosed_active}건)
        {t.data_status?.backlog?.amount != null && <> · 수주잔고 {krw(t.data_status.backlog.amount)} ({t.data_status.backlog.source_label})</>}
      </div>
      <table className="w-full text-[9.5px]">
        <thead><tr><Th>계약일</Th><Th>계약상대방</Th><Th>내용</Th><Th right>계약금액</Th><Th>기간</Th><Th>상태</Th><Th right>잔여 추정</Th></tr></thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.key}>
              <Td className="tabular whitespace-nowrap">{c.contract_date ?? '—'}</Td>
              <Td className="max-w-[150px] truncate">{c.counterparty ?? '비공개'}</Td>
              <Td className="max-w-[170px] truncate">{c.description}</Td>
              <Td right>{c.amount_disclosed ? krw(c.amount) : '비공개'}</Td>
              <Td className="tabular whitespace-nowrap text-slate-500">{c.start_date?.slice(2)}~{c.end_date?.slice(2)}</Td>
              <Td className="whitespace-nowrap">{st(c)}</Td>
              <Td right>{c.remaining_estimate != null ? krw(c.remaining_estimate) : '—'}</Td>
            </tr>
          ))}
        </tbody>
      </table>
      {book.contracts.length > rows.length && <div className="mt-0.5 text-[9px] text-slate-500">진행 중 계약 우선, 최근순 상위 {rows.length}건 표시 (전체 {book.contracts.length}건은 화면에서 확인)</div>}
    </>
  )
}

// ------------------------------------------------------------------ report narrative + exhibits
type Para = NonNullable<Results['report']>['content']['sections'][number]['paragraphs'][number]

/** 보고서 문단 중 검증 통과분만 본문에, 실패분은 부록으로. */
export function splitParas(r: Results) {
  const verified: Record<string, Para[]> = {}
  const held: { title: string; p: Para }[] = []
  for (const s of r.report?.content.sections ?? []) {
    verified[s.key] = s.paragraphs.filter((p) => p.status === 'verified')
    s.paragraphs.filter((p) => p.status !== 'verified').forEach((p) => held.push({ title: s.title, p }))
  }
  return { verified, held }
}

function citedIds(r: Results, verified: Record<string, Para[]>): string[] {
  const ids = new Set<string>()
  Object.values(verified).forEach((ps) => ps.forEach((p) => p.evidence_ids.forEach((i) => ids.add(i))))
  const fv = r.target?.fair_value
  ;[fv?.eid, fv?.confidence_eid].forEach((i) => i && ids.add(i))
  return [...ids].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
}

const CLAIM_TAG: Record<string, string> = { estimate: '추정', judgment: '판단' }

/** 분석 서술: 문단 + 근거 인용 표시(부록 C의 근거 ID) + 관련 그림·표 참조. */
function Narrative({ paras, refs, empty }: { paras?: Para[]; refs?: string; empty?: string }) {
  if (!paras?.length) return <p className="text-slate-500">{empty ?? '이 항목은 검증을 통과한 서술이 없습니다(부록 D 참조).'}</p>
  return (
    <div className="space-y-2">
      {paras.map((p) => (
        <p key={p.ref} className="text-justify text-[10.5px] leading-[1.75]">
          {CLAIM_TAG[p.claim_type] && <span className="mr-1 rounded bg-slate-100 px-1 text-[8.5px] text-slate-600">{CLAIM_TAG[p.claim_type]}</span>}
          {p.text}
          <sup className="ml-0.5 text-[7.5px] text-blue-700">[{p.evidence_ids.join(',')}]</sup>
        </p>
      ))}
      {refs && <p className="text-[9px] text-slate-500">→ {refs}</p>}
    </div>
  )
}

/** 번호가 붙는 그림·표. 캡션 위, 자료 출처 아래(리서치 보고서 양식). */
function Exhibit({ label, title, source, children, note, split }: { label: string; title: string; source: string; children: ReactNode; note?: ReactNode; split?: boolean }) {
  // split: 한 쪽보다 길 수 있는 표는 쪽을 넘겨 이어지게(빈 쪽 방지)
  return (
    <figure className={`pr-exhibit my-1 ${split ? '' : 'pr-keep'}`}>
      <figcaption className="mb-1 border-b border-slate-300 pb-0.5 text-[10px] font-semibold"><span className="mr-1.5 text-blue-800">{label}</span>{title}</figcaption>
      {children}
      <div className="mt-0.5 flex justify-between gap-3 text-[8.5px] text-slate-500"><span>{note}</span><span className="shrink-0">자료: {source}</span></div>
    </figure>
  )
}

function makeCounter() {
  let fig = 0
  let tab = 0
  return { fig: () => `그림 ${++fig}`, tab: () => `표 ${++tab}` }
}

// ------------------------------------------------------------------ page
export default function PrintReport() {
  const { projectId = '', runId = '' } = useParams()
  const [sp] = useSearchParams()
  const q = useQuery({ queryKey: ['results', projectId, runId], queryFn: () => analysisApi.results(projectId, runId) })
  const split = q.data ? splitParas(q.data) : { verified: {}, held: [] }
  const ids = q.data ? citedIds(q.data, split.verified) : []
  const ev = useQuery({ queryKey: ['evidence-print', runId, ids.join(',')], queryFn: () => analysisApi.evidence(projectId, runId, ids), enabled: ids.length > 0 })
  const ready = q.isSuccess && (ids.length === 0 || ev.isFetched)

  useEffect(() => {
    if (!q.data) return
    document.title = `${q.data.target?.name ?? 'report'}_상세분석`  // 브라우저 인쇄 저장 시 기본 파일명
  }, [q.data])
  useEffect(() => {
    if (!ready) return
    document.documentElement.dataset.reportReady = '1'
    if (sp.get('autoprint') === '1') {
      const h = setTimeout(() => window.print(), 800)
      return () => clearTimeout(h)
    }
  }, [ready, sp])

  if (q.isLoading) return <div className="p-8 text-sm text-slate-500">보고서 불러오는 중…</div>
  if (q.isError || !q.data) return <div className="p-8 text-sm text-red-600">{(q.error as Error)?.message ?? '결과를 불러오지 못했습니다.'}</div>
  const r = q.data
  const t = r.target
  const s = r.run.summary
  const V = split.verified
  const colors = colorMap(r.charts.companies, false)
  const peers = r.companies.filter((c) => !c.is_target)
  const meta = `${t?.name ?? ''} · 분석 ${fmtDate(r.run.finished_at)}`
  const n = makeCounter()
  const SRC_FIN = 'OpenDART 정기보고서, ValueScope'
  const SRC_MKT = 'KRX, OpenDART, ValueScope'

  // 그림·표 번호를 먼저 정해 본문에서 참조할 수 있게 한다
  const X = {
    fv: n.fig(), methods: n.tab(),
    quarterly: n.fig(), earnings: n.tab(),
    forward: r.charts.forward ? n.fig() : '', schedule: t?.data_status?.schedule ? n.tab() : '', contracts: r.charts.contracts_monthly?.length ? n.fig() : '',
    confidence: t?.confidence_breakdown?.length ? n.tab() : '',
    peerTable: n.tab(), multiples: n.fig(), margin: n.fig(), mcap: n.fig(),
    price: n.fig(), excess: n.fig(), attribution: n.tab(),
    issues: n.tab(),
    methodDetail: n.tab(),
  }
  const refs = (...xs: string[]) => xs.filter(Boolean).join(', ') + ' 참조'

  return (
    <EvidenceProvider projectId={projectId} runId={runId}>
      <div className="print-report bg-white text-[10.5px] leading-relaxed text-slate-900">
        {/* ===================== 표지 요약 */}
        <section className="pr-chapter">
          <header className="mb-4 border-b-[3px] border-slate-900 pb-3">
            <div className="text-[9px] uppercase tracking-wider text-slate-500">ValueScope Equity Research · 기업 가치평가 보고서</div>
            <h1 className="mt-1 text-[26px] font-bold leading-tight">{t?.name ?? '—'} <span className="text-[13px] font-normal text-slate-500">{t?.stock_code} · {t?.market}</span></h1>
            <div className="text-[9.5px] text-slate-500">
              분석 완료 {fmtDateTime(r.run.finished_at)} · 주가 기준일 {fmtDate(r.run.price_as_of)} · 비교군 v{r.run.peer_version_no} ({(s.valuation_peers ?? []).join(', ')})
            </div>
          </header>
          <SummaryBox r={r} />
          {r.report?.content.summary && (
            <div className="pr-keep mt-4 border-l-[3px] border-blue-700 bg-slate-50 px-3 py-2">
              <div className="mb-1 text-[10px] font-semibold text-blue-800">핵심 요약</div>
              <p className="text-justify text-[10.5px] leading-[1.75]">{r.report.content.summary}</p>
            </div>
          )}
          <div className="mt-4 space-y-3">
            <Exhibit label={X.fv} title="현재 주가와 평가 방법별 적정가치 범위" source="ValueScope"
              note={`막대 = 보수~낙관, 눈금 = 기준값, 빨간 선 = 현재가 · 가중치 ${Object.entries(t?.fair_value?.weights ?? {}).map(([k, w]) => `${METHOD_LABEL[k]} ${pct(w, 0)}`).join(', ')}`}>
              {r.charts.scenarios ? <FairValueChart scenarios={r.charts.scenarios} price={s.price} /> : <div className="text-slate-500">적정가치 산출 불가</div>}
            </Exhibit>
            {t && <Exhibit label={X.methods} title="평가 방법별 주당가치와 가중 결합" source="ValueScope"><MethodTable t={t} /></Exhibit>}
          </div>
          <div className="mt-4 text-[9px] text-slate-500">
            목차 — 1. 기업 종합 평가 · 2. 투자 포인트와 리스크 · 3. 동종업계 비교 · 4. 주가 움직임 · 5. 최근 이슈와 업황 · 6. 적정주가 산출 · 7. 결론과 평가 변화 조건 · 부록
          </div>
        </section>

        {/* ===================== 1. 종합 평가 */}
        <Chapter no={1} title="기업 종합 평가" sub={meta}>
          <Narrative paras={V.overall} refs={refs(X.quarterly, X.earnings)} />
          {t && (
            <>
              <Exhibit label={X.quarterly} title={`${t.name} 분기 매출·영업이익`} source="OpenDART 정기보고서" note="4분기 = 연간 − 3분기 누적">
                <QuarterlyChart rows={t.metrics.quarters} />
              </Exhibit>
              <Exhibit label={X.earnings} title="보고 이익 vs 경상·정상화 이익" source={SRC_FIN}><EarningsAdjustment v={t} /></Exhibit>
            </>
          )}
        </Chapter>

        {/* ===================== 2. 투자 포인트와 리스크 */}
        <Chapter no={2} title="투자 포인트와 리스크" sub={meta}>
          <h3 className="text-[11.5px] font-semibold text-emerald-800">2-1. 긍정적 요인</h3>
          <Narrative paras={V.positive} refs={refs(X.forward, X.schedule, X.contracts)} />
          {X.forward && r.charts.forward && (
            <Exhibit label={X.forward} title="향후 12개월 실적 시나리오 (수주잔고 납기 기반)" source="OpenDART 정기보고서·수주공시, ValueScope" note="추정치 — 공식 전망치가 아님">
              <ForwardChart forward={r.charts.forward} />
            </Exhibit>
          )}
          {X.schedule && t && <Exhibit split label={X.schedule} title="수주잔고 납기 배분" source="OpenDART 정기보고서·수주공시, ValueScope"><ScheduleTable v={t} /></Exhibit>}
          {X.contracts && t && (
            <Exhibit label={X.contracts} title="월별 신규 수주 (단일판매·공급계약 공시)" source="OpenDART" note="계약 목록은 부록 B">
              <ContractsChart monthly={r.charts.contracts_monthly!} backlog={t.data_status?.backlog?.amount} />
            </Exhibit>
          )}
          <h3 className="pt-2 text-[11.5px] font-semibold text-red-800">2-2. 부정적 요인과 불확실성</h3>
          <Narrative paras={V.negative} refs={X.confidence ? refs(X.confidence) : undefined} />
          {X.confidence && t?.confidence_breakdown && (
            <Exhibit label={X.confidence} title={`분석 신뢰도 ${s.confidence?.toFixed(0)}점 — 감점 사유와 회복 조건`} source="ValueScope">
              <table className="w-full text-[9.5px]">
                <thead><tr><Th>감점</Th><Th>사유</Th><Th>회복 조건</Th></tr></thead>
                <tbody>{t.confidence_breakdown.map((b, i) => <tr key={i}><Td className="tabular text-red-700">{b.points}</Td><Td>{b.reason}</Td><Td className="text-slate-500">{b.recover ?? '—'}</Td></tr>)}</tbody>
              </table>
            </Exhibit>
          )}
        </Chapter>

        {/* ===================== 3. 동종업계 */}
        <Chapter no={3} title="동종업계 비교" sub={meta}>
          <Narrative paras={V.peers} refs={refs(X.peerTable, X.multiples, X.margin)} />
          <Exhibit label={X.peerTable} title="비교 기업 주요 지표" source={SRC_MKT} note="EV/정상EBIT·정상 P/E = 최대 20개 분기 평균 이익 기준, 조정 PER = 경상순이익(TTM) 기준">
            <PeerCompactTable r={r} />
          </Exhibit>
          <Exhibit label={X.multiples} title="밸류에이션 배수 비교" source={SRC_MKT}>
            <div className="grid grid-cols-2 gap-x-4">
              <MultiplesChart charts={r.charts} metric="ev_ebit_norm" label="EV/정상EBIT" />
              <MultiplesChart charts={r.charts} metric="pe_norm" label="정상 P/E" />
              <MultiplesChart charts={r.charts} metric="per_adj" label="조정 PER (경상순이익 TTM)" />
              <MultiplesChart charts={r.charts} metric="pbr" label="PBR" />
            </div>
          </Exhibit>
          {t && (
            <Exhibit label={X.margin} title="영업이익률 추이 (최근 8개 분기)" source="OpenDART">
              <MarginChart series={[t, ...peers].filter((c) => c.metrics.quarters?.length).map((c) => ({ name: c.name, color: colors[c.company_id], rows: c.metrics.quarters.slice(-8), target: c.is_target }))} />
            </Exhibit>
          )}
          <Exhibit label={X.mcap} title="시가총액 비교" source="KRX"><MarketCapChart charts={r.charts} /></Exhibit>
        </Chapter>

        {/* ===================== 4. 주가 */}
        <Chapter no={4} title="주가 움직임" sub={meta}>
          <Narrative paras={V.price_move} refs={refs(X.price, X.excess, X.attribution)} />
          <Exhibit label={X.price} title="고객사와 비교기업 주가 (최근 1년, 기준=100)" source="KRX" note="분할·병합은 상장주식수 변동으로 보정">
            <PriceCompareChart charts={r.charts} />
          </Exhibit>
          <Exhibit label={X.excess} title="시장 대비 누적 초과수익률 (최근 1년)" source="KRX, ValueScope" note={`${r.charts.market_index?.label ?? ''} 대비 (공식 지수 아님)`}>
            <ExcessReturnChart charts={r.charts} />
          </Exhibit>
          <Exhibit split label={X.attribution} title="주가 변동 원인 분해 (시장·산업·개별)" source="KRX, OpenDART, ValueScope 회귀분석">
            <AttributionTable a={t?.attribution} />
          </Exhibit>
        </Chapter>

        {/* ===================== 5. 이슈 */}
        <Chapter no={5} title="최근 이슈와 업황 (공시·뉴스)" sub={meta}>
          <Narrative paras={V.issues} refs={refs(X.issues)} />
          <Exhibit split label={X.issues} title="최근 이슈 타임라인 — 고객사 · 비교기업 · 전방산업" source="OpenDART, 언론 보도(Claude 웹검색, 링크 확인)">
            <IssueTimeline issues={r.charts.issues} newsEnabled={r.charts.news_enabled} compact />
          </Exhibit>
        </Chapter>

        {/* ===================== 6. 적정주가 */}
        {t && (
          <Chapter no={6} title="적정주가 산출" sub={meta}>
            <Narrative paras={V.fair_value} refs={refs(X.methods, X.methodDetail)} />
            <Exhibit split label={X.methodDetail} title="평가 방법별 산출 내역" source="ValueScope" note="지분가치 = EV − 순차입금 − 비지배지분, 주당가치 = 지분가치 ÷ (상장주식수 − 자기주식)">
              <div className="space-y-3">
                {(['forward', 'peer', 'history'] as const).map((k) => t.methods?.[k] && <div key={k} className="pr-keep"><MethodCard m={t.methods[k]} /></div>)}
              </div>
            </Exhibit>
          </Chapter>
        )}

        {/* ===================== 7. 결론 */}
        <Chapter no={7} title="결론과 평가 변화 조건" sub={meta}>
          <h3 className="text-[11.5px] font-semibold">7-1. 결론</h3>
          <Narrative paras={V.conclusion} />
          <h3 className="pt-2 text-[11.5px] font-semibold">7-2. 평가가 바뀔 수 있는 조건</h3>
          <Narrative paras={V.triggers} />
          {r.report && (
            <div className="pr-keep mt-3 border-t border-slate-300 pt-2 text-[9px] leading-relaxed text-slate-500">
              작성: {r.report.ai_model ?? '규칙 기반'} · 검증: {r.report.review.by_ai ? `Review Agent(${r.report.review.model}) + 수치 대조` : '수치 대조'} ·
              전체 {r.report.review.paragraphs}개 문단 중 {r.report.review.paragraphs - r.report.review.unverified}개 게시, {r.report.review.unverified}개는 검증 미통과로 본문에서 제외(부록 D).
              본문의 [E숫자]는 부록 C의 근거 ID이며, '추정'은 엔진 가정에 따른 값, '판단'은 해석입니다.
            </div>
          )}
        </Chapter>

        {/* ===================== 부록 */}
        <Chapter no="부록" title="분석 가정 · 계약 목록 · 근거" sub={meta}>
          <Block title="A. 분석 가정 및 데이터 기준">
            <table className="w-full text-[9.5px]">
              <tbody>
                {Object.entries(r.run.assumptions ?? {}).map(([k, val]) => (
                  <tr key={k}><Td className="w-44 align-top text-slate-500">{ASSUMPTION_LABEL[k] ?? k}</Td><Td>{fmtAssumption(val)}</Td></tr>
                ))}
                <tr><Td className="text-slate-500">엔진 / AI</Td><Td>{r.run.model_version} / {r.run.ai_provider} {r.run.ai_model ?? ''}</Td></tr>
              </tbody>
            </table>
          </Block>
          {t && <Block title="B. 계약 공시 목록" allowSplit><ContractsCompact t={t} /></Block>}
          <Block title={`C. 본문 인용 근거 (${ids.length}건)`} allowSplit>
            {ev.data?.length ? (
              <table className="w-full table-fixed text-[8.5px] leading-snug">
                <colgroup><col className="w-[34px]" /><col className="w-[150px]" /><col className="w-[78px]" /><col className="w-[86px]" /><col /></colgroup>
                <thead><tr><Th>ID</Th><Th>항목</Th><Th right>값</Th><Th>기간·기준일</Th><Th>산출 방법 · 출처</Th></tr></thead>
                <tbody>
                  {ev.data.map((e) => (
                    <tr key={e.eid} className="pr-keep align-top">
                      <Td className="font-mono">{e.eid}</Td>
                      <Td>{e.label}</Td>
                      <Td right>{e.display ?? '—'}</Td>
                      <Td className="text-slate-600">{e.period ?? e.as_of ?? '—'}</Td>
                      <Td className="text-slate-600">
                        {e.method && <div>{e.method.length > 180 ? `${e.method.slice(0, 180)}…` : e.method}</div>}
                        <div className="text-slate-500">{e.source}{e.rcept_no && ` · 접수번호 ${e.rcept_no}`}</div>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="text-slate-500">{ev.isLoading ? '근거 불러오는 중…' : '인용된 근거가 없습니다.'}</div>}
          </Block>
          {split.held.length > 0 && (
            <Block title={`D. 검증 미통과 서술 (${split.held.length}건 — 게시 보류)`} allowSplit>
              <div className="space-y-2 text-[9px]">
                {split.held.map(({ title, p }) => (
                  <div key={p.ref} className="pr-keep border-l-2 border-amber-400 pl-2">
                    <div className="text-slate-500">{title} · {p.ref}</div>
                    <div className="text-slate-500 line-through decoration-amber-400/70">{p.text}</div>
                    <div className="text-amber-800">사유: {p.issues.filter((i) => i.severity === 'high').map((i) => i.message).join(' / ')}</div>
                  </div>
                ))}
              </div>
            </Block>
          )}
          <div className="pr-keep border-t border-slate-300 pt-2 text-[9px] leading-relaxed text-slate-500">
            본 보고서는 OpenDART 공시·KRX 시세 등 공개 데이터와 언론 보도(보조 근거)를 바탕으로 ValueScope가 계산한 분석 자료이며, 투자 권유나 매매 추천이 아닙니다.
            추정치(수주 납기 기반 실적, 적정가치 범위)는 명시된 가정에 따른 결과로 실제와 다를 수 있습니다. 공시로 확인되지 않은 보도는 사실로 단정하지 않았습니다.
          </div>
        </Chapter>
      </div>
    </EvidenceProvider>
  )
}
