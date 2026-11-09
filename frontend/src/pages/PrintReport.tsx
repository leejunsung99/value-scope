// 인쇄·PDF 전용 보고서 (/print/projects/:projectId/runs/:runId).
// 화면 대시보드를 그대로 인쇄하지 않고, 보고서 구조(장 → 블록)를 따로 정해 A4 세로로 조판한다.
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
import { AttributionTable, EarningsAdjustment, IssueTimeline, MethodCard, ReportSection, ScheduleTable } from './Results'

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

function citedIds(r: Results): string[] {
  const ids = new Set<string>()
  for (const s of r.report?.content.sections ?? []) for (const p of s.paragraphs) p.evidence_ids.forEach((i) => ids.add(i))
  const t = r.target
  const fv = t?.fair_value
  ;[fv?.eid, fv?.confidence_eid, t?.methods?.peer?.eid, t?.methods?.forward?.eid, t?.methods?.history?.eid].forEach((i) => i && ids.add(i))
  return [...ids].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
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

function Th({ children, right }: { children?: ReactNode; right?: boolean }) {
  return <th className={`border-b border-slate-400 px-1.5 py-1 font-semibold text-slate-600 ${right ? 'text-right' : 'text-left'}`}>{children}</th>
}
function Td({ children, right, strong, className = '' }: { children?: ReactNode; right?: boolean; strong?: boolean; className?: string }) {
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

function MethodTable({ t }: { t: ValuationResult }) {
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

function PeerCompactTable({ r }: { r: Results }) {
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

// ------------------------------------------------------------------ page
export default function PrintReport() {
  const { projectId = '', runId = '' } = useParams()
  const [sp] = useSearchParams()
  const q = useQuery({ queryKey: ['results', projectId, runId], queryFn: () => analysisApi.results(projectId, runId) })
  const ids = q.data ? citedIds(q.data) : []
  const ev = useQuery({ queryKey: ['evidence-print', runId, ids.join(',')], queryFn: () => analysisApi.evidence(projectId, runId, ids), enabled: ids.length > 0 })
  const ready = q.isSuccess && (ids.length === 0 || ev.isFetched)

  useEffect(() => {
    if (!q.data) return
    document.title = `ValueScope 분석 보고서 — ${q.data.target?.name ?? ''} (${(q.data.run.finished_at ?? '').slice(0, 10)})`
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
  const colors = colorMap(r.charts.companies, false)
  const peers = r.companies.filter((c) => !c.is_target)
  const meta = `${t?.name ?? ''} · 분석 ${fmtDate(r.run.finished_at)}`

  return (
    <EvidenceProvider projectId={projectId} runId={runId}>
      <div className="print-report bg-white text-[10.5px] leading-relaxed text-slate-900">
        {/* ===================== 1. 요약 */}
        <Chapter no={1} title="투자 판단 요약" first before={
          <header className="pr-keep mb-5 border-b border-slate-300 pb-3">
            <div className="text-[9px] uppercase tracking-wider text-slate-500">ValueScope · 기업 가치평가 및 동종업계 분석 보고서</div>
            <h1 className="mt-0.5 text-[24px] font-bold leading-tight">{t?.name ?? '—'} <span className="text-[13px] font-normal text-slate-500">{t?.stock_code} · {t?.market}</span></h1>
            <div className="text-[9.5px] text-slate-500">
              분석 완료 {fmtDateTime(r.run.finished_at)} · 주가 기준일 {fmtDate(r.run.price_as_of)} · 비교군 v{r.run.peer_version_no} ({(s.valuation_peers ?? []).join(', ')})
            </div>
          </header>}>
          <SummaryBox r={r} />
          {r.report?.content.summary && (
            <Block title="핵심 요약">
              <p className="text-[10.5px]">{r.report.content.summary}</p>
            </Block>
          )}
          <Block title="현재 주가와 적정가치 범위" note={`막대 = 보수~낙관, 눈금 = 기준값, 빨간 선 = 현재가. 가중치: ${Object.entries(t?.fair_value?.weights ?? {}).map(([k, w]) => `${METHOD_LABEL[k]} ${pct(w, 0)}`).join(', ')}`}>
            {r.charts.scenarios ? <FairValueChart scenarios={r.charts.scenarios} price={s.price} /> : <div className="text-slate-500">적정가치 산출 불가</div>}
          </Block>
          {!!t?.confidence_breakdown?.length && (
            <Block title={`분석 신뢰도 ${s.confidence?.toFixed(0)}점 — 감점 내역`}>
              <table className="w-full text-[9.5px]">
                <thead><tr><Th>감점</Th><Th>사유</Th><Th>회복 조건</Th></tr></thead>
                <tbody>{t.confidence_breakdown.map((b, i) => <tr key={i}><Td className="tabular text-red-700">{b.points}</Td><Td>{b.reason}</Td><Td className="text-slate-500">{b.recover ?? '—'}</Td></tr>)}</tbody>
              </table>
            </Block>
          )}
        </Chapter>

        {/* ===================== 2. 가치평가 */}
        {t && (
          <Chapter no={2} title="가치평가 방법별 결과" sub={meta}>
            <Block title="방법별 주당가치와 가중 결합" note="지분가치 = EV − 순차입금 − 비지배지분, 주당가치 = 지분가치 ÷ (상장주식수 − 자기주식). 각 방법은 독립적으로 산출한 뒤 가중 결합합니다.">
              <MethodTable t={t} />
            </Block>
            {(['forward', 'peer', 'history'] as const).map((k) => t.methods?.[k] && (
              <Block key={k}><MethodCard m={t.methods[k]} /></Block>
            ))}
            <Block title="적정가치 산출 근거 (AI 서술)" allowSplit><ReportSection report={r.report} keys={['fair_value']} /></Block>
          </Chapter>
        )}

        {/* ===================== 3. 이익 기준 */}
        {t && (
          <Chapter no={3} title="이익 기준과 향후 실적 시나리오" sub={meta}>
            <Block><EarningsAdjustment v={t} /></Block>
            {(r.charts.forward || t.data_status?.schedule) && (
              <Block title="향후 12개월 실적 시나리오 (수주잔고 납기 기반)" note="추정치 — 공식 전망치가 아닙니다. 매출은 납기(계약 종료일)에 인식된다고 가정했습니다." allowSplit>
                {r.charts.forward && <div className="pr-keep"><ForwardChart forward={r.charts.forward} /></div>}
                <ScheduleTable v={t} />
              </Block>
            )}
          </Chapter>
        )}

        {/* ===================== 4. 동종업계 */}
        <Chapter no={4} title="동종업계 비교" sub={meta}>
          <Block title="비교 기업 지표" note="EV/정상EBIT·정상 P/E는 최대 20개 분기(5년) 평균 이익 기준, 조정 PER은 경상순이익(TTM) 기준입니다. N/A는 적자·데이터 부족으로 비교하지 않은 값입니다.">
            <PeerCompactTable r={r} />
          </Block>
          <Block title="밸류에이션 배수 비교">
            <div className="grid grid-cols-2 gap-x-4">
              <MultiplesChart charts={r.charts} metric="ev_ebit_norm" label="EV/정상EBIT" />
              <MultiplesChart charts={r.charts} metric="pe_norm" label="정상 P/E" />
              <MultiplesChart charts={r.charts} metric="per_adj" label="조정 PER (경상순이익 TTM)" />
              <MultiplesChart charts={r.charts} metric="pbr" label="PBR" />
            </div>
          </Block>
          <Block title="시가총액 비교"><MarketCapChart charts={r.charts} /></Block>
          <Block title="동종업계 대비 평가 (AI 서술)" allowSplit><ReportSection report={r.report} keys={['peers']} /></Block>
        </Chapter>

        {/* ===================== 5. 주가 */}
        <Chapter no={5} title="주가 동향과 변동 요인" sub={meta}>
          <Block title="고객사와 비교기업 주가 (최근 1년, 기준=100)"><PriceCompareChart charts={r.charts} /></Block>
          <Block title="시장 대비 초과수익률 (최근 1년)" note={`${r.charts.market_index?.label ?? ''} 대비 누적 상대수익률(%p). 공식 KRX 지수가 아닌 시가총액 가중 대용치입니다.`}>
            <ExcessReturnChart charts={r.charts} />
          </Block>
          <Block title="주가 변동 원인 분해 (시장·산업·개별)" allowSplit><AttributionTable a={t?.attribution} /></Block>
          <Block title="주가 흐름 해석 (AI 서술)" allowSplit><ReportSection report={r.report} keys={['price_move']} /></Block>
        </Chapter>

        {/* ===================== 6. 이슈 */}
        <Chapter no={6} title="최근 이슈와 업황 (공시·뉴스)" sub={meta}>
          <Block allowSplit><IssueTimeline issues={r.charts.issues} newsEnabled={r.charts.news_enabled} compact /></Block>
          <Block title="이슈 해석 (AI 서술)" allowSplit><ReportSection report={r.report} keys={['issues']} /></Block>
        </Chapter>

        {/* ===================== 6. 실적·수주 */}
        {t && (
          <Chapter no={7} title="실적 추이와 수주" sub={meta}>
            <Block title={`${t.name} 분기 매출·영업이익`} note="OpenDART 정기보고서. 4분기 = 연간 − 3분기 누적."><QuarterlyChart rows={t.metrics.quarters} /></Block>
            <Block title="영업이익률 추이 (최근 8개 분기)">
              <MarginChart series={[t, ...peers].filter((c) => c.metrics.quarters?.length).map((c) => ({ name: c.name, color: colors[c.company_id], rows: c.metrics.quarters.slice(-8), target: c.is_target }))} />
            </Block>
            {!!r.charts.contracts_monthly?.length && (
              <Block title="월별 신규 수주 (단일판매·공급계약 공시)"><ContractsChart monthly={r.charts.contracts_monthly} backlog={t.data_status?.backlog?.amount} /></Block>
            )}
            <Block title="계약 공시 목록" allowSplit><ContractsCompact t={t} /></Block>
          </Chapter>
        )}

        {/* ===================== 7. AI 보고서 */}
        {r.report && (
          <Chapter no={8} title="근거 기반 종합 분석" sub={meta}>
            <div className="text-[9.5px] text-slate-500">
              AI 서술({r.report.ai_model ?? '규칙 기반'})은 근거 ID로 연결된 값만 인용하도록 검증되었습니다.
              검증 {r.report.review.by_ai ? `AI(${r.report.review.model}) + 결정적 검증` : '결정적 검증'} · 문단 {r.report.review.paragraphs}개 중 검증 실패 {r.report.review.unverified}개(취소선 표시).
            </div>
            <Block allowSplit><ReportSection report={r.report} keys={['overall', 'positive', 'negative', 'conclusion', 'triggers']} /></Block>
          </Chapter>
        )}

        {/* ===================== 부록 */}
        <Chapter no="부록" title="분석 가정과 인용 근거" sub={meta}>
          <Block title="분석 가정 및 데이터 기준">
            <table className="w-full text-[9.5px]">
              <tbody>
                {Object.entries(r.run.assumptions ?? {}).map(([k, val]) => (
                  <tr key={k}><Td className="w-44 align-top text-slate-500">{ASSUMPTION_LABEL[k] ?? k}</Td><Td>{fmtAssumption(val)}</Td></tr>
                ))}
                <tr><Td className="text-slate-500">엔진 / AI</Td><Td>{r.run.model_version} / {r.run.ai_provider} {r.run.ai_model ?? ''}</Td></tr>
              </tbody>
            </table>
          </Block>
          <Block title={`보고서 인용 근거 (${ids.length}건)`} allowSplit>
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
          <div className="pr-keep border-t border-slate-300 pt-2 text-[9px] leading-relaxed text-slate-500">
            본 보고서는 OpenDART 공시·KRX 시세 등 공개 데이터를 바탕으로 ValueScope가 계산한 분석 자료이며, 투자 권유나 매매 추천이 아닙니다.
            추정치(수주 납기 기반 실적, 적정가치 범위)는 명시된 가정에 따른 결과로 실제와 다를 수 있습니다.
          </div>
        </Chapter>
      </div>
    </EvidenceProvider>
  )
}
