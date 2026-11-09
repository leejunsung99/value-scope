import { useQuery } from '@tanstack/react-query'
import { Fragment, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  analysisApi, krw, METHOD_LABEL, mult, pct, SCENARIO_LABEL, VERDICT_TONE, won,
  type Attribution, type MethodResult, type Report, type Results, type ValuationResult,
} from '../apiAnalysis'
import {
  colorMap, ContractsChart, ExcessReturnChart, FairValueChart, ForwardChart, HistoryChart, MarginChart, MarketCapChart,
  MultiplesChart, PriceCompareChart, QuarterlyChart, useDark,
} from '../charts'
import { Badge, Card, CATEGORY_LABELS, Empty, fmtDate, fmtDateTime, Notice, Spinner, USE_TYPE_LABELS } from '../ui'
import { EvidenceLink, EvidenceProvider } from './Evidence'
import { useProject } from './ProjectLayout'

function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold">{title}</h2>{right}</div>
      {children}
    </Card>
  )
}

function Kpi({ label, value, sub, eids }: { label: string; value: ReactNode; sub?: ReactNode; eids?: (string | undefined | null)[] }) {
  return (
    <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="tabular mt-0.5 text-lg font-bold">{eids ? <EvidenceLink ids={eids}>{value}</EvidenceLink> : value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  )
}

export function ReportSection({ report, keys }: { report: Report | null; keys: string[] }) {
  if (!report) return null
  const secs = report.content.sections.filter((s) => keys.includes(s.key))
  return (
    <div className="mt-3 space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
      {secs.map((s) => (
        <div key={s.key}>
          <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-slate-600 dark:text-slate-300">
            {s.title} <Badge tone="violet">{report.status === 'generated' ? `AI 분석 · ${report.ai_model ?? ''}` : '규칙 기반'}</Badge>
          </div>
          {s.missing && <div className="text-xs text-slate-500">이 섹션은 작성되지 않았습니다.</div>}
          {s.paragraphs.map((p) => (
            <div key={p.ref} className={`mb-1.5 rounded px-2 py-1 text-sm leading-relaxed ${p.status === 'unverified' ? 'border border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40' : ''}`}>
              {p.status === 'unverified' && (
                <div className="mb-0.5 text-[11px] font-semibold text-amber-800 dark:text-amber-300" title={p.issues.map((i) => `[${i.by}] ${i.type}: ${i.message}`).join('\n')}>
                  ⚠ 검증 실패 — 확정 결과로 게시하지 않음: {p.issues.filter((i) => i.severity === 'high').map((i) => i.message).join(' / ')}
                </div>
              )}
              <span className={p.status === 'unverified' ? 'text-slate-500 line-through decoration-amber-400/60' : ''}>{p.text}</span>{' '}
              <span className="text-[11px]">
                <Badge tone={p.claim_type === 'fact' ? 'green' : p.claim_type === 'estimate' ? 'violet' : 'slate'}>
                  {p.claim_type === 'fact' ? '사실' : p.claim_type === 'estimate' ? '추정' : 'AI 판단'}
                </Badge>{' '}
                <EvidenceLink ids={p.evidence_ids} title={`${s.title} — 근거`}>근거 {p.evidence_ids.join(', ')}</EvidenceLink>
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function MethodCard({ m }: { m?: MethodResult }) {
  if (!m) return null
  return (
    <div className="rounded-md border border-slate-200 p-3 text-xs dark:border-slate-800">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold">{m.label}</div>
        {m.available ? <Badge tone="green">적용</Badge> : <Badge>적용 불가</Badge>}
      </div>
      {m.available && (
        <table className="tabular mt-2 w-full">
          <tbody>{(['conservative', 'base', 'optimistic'] as const).map((s) => (
            <tr key={s}><td className="text-slate-500">{SCENARIO_LABEL[s]}</td><td className="text-right font-medium">{won(m.scenarios[s]?.per_share)}</td></tr>
          ))}</tbody>
        </table>
      )}
      {m.inputs?.map((u) => (
        <div key={u.multiple} className="mt-2 text-slate-600 dark:text-slate-400">
          <b>{u.multiple}</b>{u.fallback && ' (대체 배수)'} — 하위/중앙/상위 {u.quantiles.conservative.toFixed(1)} / {u.quantiles.base.toFixed(1)} / {u.quantiles.optimistic.toFixed(1)}배
          {u.peers && <div>비교기업(가중치): {u.peers.map((p) => `${p.name} ${p.value.toFixed(1)}${p.weight != null ? ` (${p.weight.toFixed(2)})` : ''}`).join(', ')}</div>}
          {!!u.dropped?.length && <div className="text-amber-700 dark:text-amber-400">이상치 제외: {u.dropped.map((p) => `${p.name} ${p.value.toFixed(1)}`).join(', ')}</div>}
          {u.n && <div>과거 월말 관측 {u.n}개 (범위 {u.range?.[0].toFixed(1)}~{u.range?.[1].toFixed(1)}배)</div>}
        </div>
      ))}
      {m.quality?.percentile != null && (
        <div className="mt-2 text-slate-600 dark:text-slate-400">고객사 품질 백분위 {pct(m.quality.percentile, 0)} (적용 {pct(m.quality_percentile_used ?? null, 0)}): {Object.values(m.quality.metrics).map((q) => `${q.label} ${pct(q.percentile, 0)}`).join(' · ')}</div>
      )}
      {m.assumptions?.backlog && (
        <div className="mt-2 space-y-0.5 text-slate-600 dark:text-slate-400">
          <div>공식 수주잔고 {krw(m.assumptions.backlog)} ({m.assumptions.backlog_as_of})</div>
          <div>향후 12개월 납기 도래 매출: {Object.entries(m.assumptions.forward_revenue ?? {}).map(([k, v]) => `${SCENARIO_LABEL[k as 'base']} ${krw(v)}`).join(' · ')}</div>
          <div>영업이익률: {Object.entries(m.assumptions.op_margin ?? {}).map(([k, v]) => `${SCENARIO_LABEL[k as 'base']} ${pct(v)}`).join(' · ')} ({m.assumptions.op_margin_basis})</div>
          <div>EV/정상EBIT {m.assumptions.multiple?.toFixed(1)}배 — {m.assumptions.multiple_source}</div>
          {m.assumptions.normalization && <div>{m.assumptions.normalization} (최근 8분기 영업이익 {krw(m.assumptions.op_last8)})</div>}
        </div>
      )}
      {m.notes.map((n, i) => <div key={i} className="mt-1 text-amber-700 dark:text-amber-400">· {n}</div>)}
      {m.eid && <div className="mt-1"><EvidenceLink ids={[m.eid]}>계산 과정 보기</EvidenceLink></div>}
    </div>
  )
}

export function AttributionTable({ a }: { a?: Attribution }) {
  if (!a) return null
  if (!a.available) return <div className="text-xs text-slate-500">주가 변동 분해 불가: {a.reason}</div>
  return (
    <div className="text-xs">
      <table className="tabular w-full">
        <thead className="text-left text-[11px] text-slate-500"><tr><th className="py-1">기간</th><th className="text-right">합계</th><th className="text-right">시장 영향</th><th className="text-right">동종업계·산업</th><th className="text-right">개별기업·미확인</th></tr></thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {Object.entries(a.windows ?? {}).map(([k, w]) => (
            <tr key={k}><td className="py-1">{k}</td><td className="text-right font-semibold">{pct(w.total)}</td><td className="text-right">{pct(w.market)}</td><td className="text-right">{pct(w.industry)}</td><td className="text-right">{pct(w.individual)}</td></tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 text-[11px] text-slate-500">
        β시장 {a.model?.beta_market.toFixed(2)} (t={a.model?.t_market.toFixed(1)}) · β산업 {a.model?.beta_industry?.toFixed(2) ?? 'N/A'} (t={a.model?.t_industry?.toFixed(1) ?? '-'}) · R² {a.model?.r2.toFixed(2)} · 관측 {a.n}일 ({a.period?.join(' ~ ')}) · 시장: {a.market_label}
      </div>
      <div className="text-[11px] text-slate-500">{a.note}</div>
      {!!a.abnormal_days?.length && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-semibold">개별 요인 이상 변동일 {a.abnormal_days.length}건</summary>
          <ul className="mt-1 space-y-1">
            {a.abnormal_days.map((d) => (
              <li key={d.date}>
                <span className="tabular">{d.date}</span> 수익률 {pct(d.return)} (개별 {pct(d.idiosyncratic)}) — <span className={d.disclosures.length ? 'text-blue-700 dark:text-blue-300' : 'text-slate-500'}>{d.classification}</span>
                {d.disclosures.map((x) => <a key={x.rcept_no} href={x.url} target="_blank" rel="noreferrer" className="ml-2 text-blue-600 hover:underline">{fmtDate(x.rcept_dt)} {x.report_nm}</a>)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

export function ContractsTable({ v }: { v: ValuationResult }) {
  const book = v.data_status?.contracts
  const bl = v.data_status?.backlog
  if (!book) return null
  return (
    <div className="text-xs">
      <div className="mb-2 flex flex-wrap gap-3 text-slate-600 dark:text-slate-400">
        <span>공시 {book.filings_total}건 → 계약 {book.contracts_total}건 (정정공시 {book.duplicates_merged}건 병합, 중복 집계 방지)</span>
        <span>진행 중 {book.active_count}건 (금액 비공개 {book.undisclosed_active}건){book.lapsed_count ? ` · 기간 경과·완료 미공시 ${book.lapsed_count}건` : ''}</span>
        {bl?.amount != null && <span>수주잔고 <EvidenceLink ids={[bl.eid]}>{krw(bl.amount)}</EvidenceLink> — {bl.source_label}</span>}
      </div>
      <div className="max-h-72 overflow-y-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-white text-left text-[11px] text-slate-500 dark:bg-slate-900"><tr><th className="py-1">계약일</th><th>계약상대방</th><th>내용</th><th className="text-right">계약금액</th><th>기간</th><th>상태</th><th className="text-right">잔여 추정</th><th>공시</th></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {book.contracts.map((c) => (
              <tr key={c.key}>
                <td className="tabular py-1">{c.contract_date ?? '—'}</td>
                <td className="max-w-[160px] truncate" title={c.counterparty ?? ''}>{c.counterparty ?? '비공개'}</td>
                <td className="max-w-[200px] truncate" title={c.description ?? ''}>{c.description}</td>
                <td className="tabular text-right">{c.amount_disclosed ? krw(c.amount) : <span className="text-amber-700">비공개{c.withheld_until ? `(~${c.withheld_until})` : ''}</span>}</td>
                <td className="tabular text-slate-500">{c.start_date?.slice(2)}~{c.end_date?.slice(2)}</td>
                <td>{c.status === 'active' ? (c.end_undetermined ? <Badge tone="amber">진행·납기 미정</Badge> : <Badge tone="blue">진행</Badge>) : c.status === 'completed' ? <Badge tone="green">이행완료</Badge> : c.status === 'lapsed' ? <Badge>기간 경과·완료 미공시</Badge> : <Badge tone="red">해지</Badge>}</td>
                <td className="tabular text-right text-slate-500">{c.remaining_estimate != null ? krw(c.remaining_estimate) : '—'}</td>
                <td><a href={c.latest_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{c.filings > 1 ? `${c.filings}건(정정 포함)` : '원문'}</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-1 text-[11px] text-slate-500">잔여 추정 = 계약금액 × 남은 계약기간 비율 (추정치 — 실제 매출 인식과 다를 수 있음). 공시되지 않은 수주는 포함되지 않습니다.</div>
    </div>
  )
}

export function EarningsAdjustment({ v }: { v: ValuationResult }) {
  const m = v.metrics.values as Record<string, number | null | undefined>
  const e = v.metrics.eids
  if (m.core_ni_ttm == null && m.ebit_norm == null) return null
  const row = (label: string, value: string, eid?: string, sub?: string) => (
    <tr><td className="py-1 text-slate-500">{label}</td><td className="tabular text-right font-medium">{eid ? <EvidenceLink ids={[eid]}>{value}</EvidenceLink> : value}</td><td className="pl-3 text-[11px] text-slate-500">{sub}</td></tr>
  )
  return (
    <Section title={`${v.name} 이익 조정 내역 — 보고 이익 vs 경상·정상화 이익`}>
      <div className="grid gap-6 md:grid-cols-2">
        <table className="w-full text-xs">
          <thead><tr><th colSpan={3} className="pb-1 text-left text-[11px] font-semibold">① 경상순이익 (최근 4개 분기)</th></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {row('영업이익', krw(m.operating_income_ttm), e.operating_income_ttm)}
            {row('+ 이자수취 − 이자지급', krw(m.net_interest_ttm), undefined, '현금흐름표')}
            {row('× (1 − 세율)', pct(m.tax_rate), e.tax_rate)}
            {row('= 경상순이익', krw(m.core_ni_ttm), e.core_ni_ttm)}
            {row('보고 지배순이익', krw(m.reported_ni_ttm))}
            {row('영업외 비경상 손익(차이)', krw(m.non_recurring_gap), e.non_recurring_gap, `금융수익 ${krw(m.finance_income_ttm)} · 금융원가 ${krw(m.finance_costs_ttm)}`)}
            {row('PER 보고 → 조정', `${mult(m.per)} → ${mult(m.per_adj)}`)}
          </tbody>
        </table>
        <table className="w-full text-xs">
          <thead><tr><th colSpan={3} className="pb-1 text-left text-[11px] font-semibold">② 정상화 이익 (12개 분기 평균{m.norm_window ? ` · ${m.norm_window}` : ''})</th></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {row('정상 EBIT (연평균 영업이익)', krw(m.ebit_norm), e.ebit_norm)}
            {row('12분기 영업이익률', pct(m.margin_norm), e.margin_norm, `TTM ${pct(m.op_margin)}`)}
            {row('정상 순이익', krw(m.ni_core_norm), e.ni_core_norm)}
            {row('EV/EBIT TTM → 정상', `${mult(m.ev_ebit)} → ${mult(m.ev_ebit_norm)}`)}
            {row('정상 P/E', mult(m.pe_norm))}
          </tbody>
        </table>
      </div>
      {v.metrics.na?.ebit_norm && <div className="mt-1 text-[11px] text-amber-700">{v.metrics.na.ebit_norm}</div>}
    </Section>
  )
}

export function ScheduleTable({ v }: { v: ValuationResult | null }) {
  const s = v?.data_status?.schedule
  if (!s) return null
  if (!s.available) return <div className="text-xs text-slate-500">납기 기반 추정 불가: {s.reason}</div>
  return (
    <div className="mt-2 text-xs">
      <div className="mb-1 text-slate-600 dark:text-slate-400">
        공식 수주잔고 {krw(s.official_backlog)}({s.as_of}) = 공시 계약 잔여 {krw(s.disclosed_remaining)} + 금액 비공개 {krw(s.undisclosed_pool)}
        (비공개 계약 {s.undisclosed_contracts}건에 계약기간 비례 배분) · 기간 {s.horizon?.join(' ~ ')}
        {s.lumpiness != null && <> · 분기 매출 변동계수 {s.lumpiness.toFixed(2)} (납품 시점 인식 근거)</>}
        {s.eid && <> · <EvidenceLink ids={[s.eid]}>근거</EvidenceLink></>}
      </div>
      <div className="mb-1 flex gap-3">{(['conservative', 'base', 'optimistic'] as const).map((k) => <span key={k}>{SCENARIO_LABEL[k]} <b className="tabular">{krw(s.scenarios?.[k].revenue)}</b></span>)}</div>
      <table className="w-full">
        <thead className="text-left text-[11px] text-slate-500"><tr><th className="py-1">계약</th><th>계약일</th><th>납기</th><th className="text-right">잔고(추정)</th><th>금액</th></tr></thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {s.pieces?.map((p, i) => (
            <tr key={i}><td className="max-w-[220px] truncate py-0.5" title={p.label}>{p.label}</td><td className="tabular">{p.contract_date ?? '—'}</td>
              <td className="tabular">{p.end ?? '—'}{p.end_assumed && ' (가정)'}</td><td className="tabular text-right">{krw(p.amount)}</td>
              <td>{p.disclosed ? <Badge tone="green">공시</Badge> : <Badge tone="amber">비공개·배분{p.weight != null ? ` ${(p.weight * 100).toFixed(0)}%` : ''}</Badge>}</td></tr>
          ))}
        </tbody>
      </table>
      {!!s.undetermined?.length && (
        <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          납기 미정 계약(향후 12개월 매출에서 제외, 합계 {krw(s.undetermined_amount)}): {s.undetermined.map((u) => `${u.label} ${u.amount != null ? krw(u.amount) : '금액 비공개'} — 납기 변경 이력 ${u.end_history.join(' → ')} → 미정`).join(' / ')}
        </div>
      )}
      {s.notes?.map((n, i) => <div key={i} className="mt-1 text-[11px] text-slate-500">· {n}</div>)}
    </div>
  )
}

function useLatestRun() {
  const [sp] = useSearchParams()
  const { project } = useProject()
  return sp.get('run') ?? project.peer_state.latest_result_run_id ?? null
}

export function Step3Results() {
  const { project } = useProject()
  const runId = useLatestRun()
  const q = useQuery({ queryKey: ['results', project.id, runId], queryFn: () => analysisApi.results(project.id, runId!), enabled: !!runId })
  if (!runId) return <Empty title="분석 결과가 없습니다"><Link to="../analysis" className="text-blue-600 underline">STEP 2</Link>에서 기업 분석을 실행하세요. 분석 완료 전에는 어떤 평가값도 표시하지 않습니다.</Empty>
  if (q.isLoading) return <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> 결과 불러오는 중…</div>
  if (q.isError || !q.data) return <Notice tone="red">{(q.error as Error)?.message}</Notice>
  return <EvidenceProvider projectId={project.id} runId={runId}><Dashboard r={q.data} projectId={project.id} /></EvidenceProvider>
}

function Dashboard({ r, projectId }: { r: Results; projectId: string }) {
  const dark = useDark()
  const t = r.target
  const s = r.run.summary
  const fv = t?.fair_value

  const eids = t?.metrics.eids ?? {}
  const colors = colorMap(r.charts.companies, dark)
  const valPeers = r.companies.filter((c) => !c.is_target)
  const verdict = s.verdict ?? '판단 유보'
  return (
    <div className="space-y-4">
      {r.run.status === 'partial' && <Notice>일부 기업·항목의 최신화에 실패했습니다. STEP 2 실행 로그에서 실패 항목을 확인하세요.</Notice>}
      {/* ---------------- 5-1 종합 평가 */}
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-xs text-slate-500">종합 평가 · 분석 {fmtDateTime(r.run.finished_at)} · 주가 기준일 {fmtDate(r.run.price_as_of)} · 비교군 v{r.run.peer_version_no}</div>
            <div className="mt-1 flex items-center gap-3">
              <span className={`rounded-md px-3 py-1 text-2xl font-bold ${{ 저평가: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300', 고평가: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300', 적정: 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300' }[verdict] ?? 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300'}`}>{verdict}</span>
              <div className="text-sm">
                {s.verdict_reason && <div className="text-slate-500">판단 유보 사유: {s.verdict_reason}</div>}
                <div>분석 신뢰도 <EvidenceLink ids={[fv?.confidence_eid]}><b>{s.confidence?.toFixed(0)}점</b></EvidenceLink>/100</div>
              </div>
            </div>
          </div>
          <div className="max-w-xl text-sm text-slate-600 dark:text-slate-300">{r.report?.content.summary}</div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
          <Kpi label="현재 주가" value={won(s.price)} sub={`기준일 ${s.price_date ?? '—'}`} eids={[eids.price]} />
          <Kpi label="시가총액" value={krw(s.market_cap)} eids={[eids.market_cap]} />
          <Kpi label="적정주가(보수)" value={won(fv?.scenarios?.conservative)} eids={[fv?.eid]} />
          <Kpi label="적정주가(기준)" value={won(fv?.scenarios?.base)} eids={[fv?.eid]} />
          <Kpi label="적정주가(낙관)" value={won(fv?.scenarios?.optimistic)} eids={[fv?.eid]} />
          <Kpi label="현재가 대비 괴리율(기준)" value={pct(s.upside_base)} sub="(적정−현재)/현재" />
          <Kpi label="동종업계 상대평가" value={won(t?.methods?.peer?.scenarios?.base?.per_share)} sub={t?.methods?.peer?.available ? `비교기업 ${t.methods.peer.peer_count}개` : '적용 불가'} eids={[t?.methods?.peer?.eid]} />
        </div>
        {fv?.envelope && (
          <div className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-950 dark:text-slate-300">
            평가 방법별 전체 범위: <b className="tabular">{won(fv.envelope.low)} ~ {won(fv.envelope.high)}</b>
            {fv.method_dispersion != null && <> · 방법 간 기준값 차이 <b>{fv.method_dispersion.toFixed(1)}배</b></>}
            {verdict === '판단 유보' && <> — 위 가중 결합값은 참고용이며, 방법 간 결론이 엇갈려 고평가·저평가 판단을 내리지 않았습니다.</>}
          </div>
        )}
        {!!t?.confidence_breakdown?.length && (
          <table className="mt-3 w-full text-[11px]">
            <thead className="text-left text-slate-500"><tr><th className="w-10 py-0.5">감점</th><th>사유</th><th>회복 조건</th></tr></thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {t.confidence_breakdown.map((b, i) => (
                <tr key={i}><td className="tabular py-0.5 text-red-600">{b.points}</td><td className="pr-3">{b.reason}</td><td className="text-slate-500">{b.recover ?? '—'}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* ---------------- 적정가치 */}
      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="현재 주가와 적정가치 범위">
          {r.charts.scenarios ? <FairValueChart scenarios={r.charts.scenarios} price={s.price} /> : <div className="text-sm text-slate-500">적정가치 산출 불가</div>}
          <div className="text-[11px] text-slate-500">막대 = 보수~낙관 범위, 세로 눈금 = 기준값, 빨간 선 = 현재가. 가중치: {Object.entries(fv?.weights ?? {}).map(([k, w]) => `${METHOD_LABEL[k]} ${pct(w, 0)}`).join(', ')}</div>
          <ReportSection report={r.report} keys={['fair_value']} />
        </Section>
        <Section title="가치평가 방법별 결과 (독립 검토)">
          <div className="grid gap-2 md:grid-cols-3">
            <MethodCard m={t?.methods?.forward} /><MethodCard m={t?.methods?.peer} /><MethodCard m={t?.methods?.history} />
          </div>
          <div className="mt-2 text-[11px] text-slate-500">지분가치 = EV − 순차입금 − 비지배지분, 주당가치 = 지분가치 ÷ (상장주식수 − 자기주식). 전환사채·교환사채는 부채로 처리(희석 미반영 — 보수적).</div>
        </Section>
      </div>

      {t && <EarningsAdjustment v={t} />}

      {(r.charts.forward || t?.data_status?.schedule) && (
        <Section title="예상 실적 시나리오 (수주잔고 납기 기반, 향후 12개월)">
          {r.charts.forward && <ForwardChart forward={r.charts.forward} />}
          <ScheduleTable v={t} />
          <div className="text-[11px] text-slate-500">추정치 — 공식 전망치가 아닙니다. 매출은 납기(계약 종료일)에 인식된다고 가정했습니다.</div>
        </Section>
      )}

      {/* ---------------- 주가 */}
      <Section title="기준 기업과 동종업계 주가 비교 (기준=100)">
        <PriceCompareChart charts={r.charts} />
        <ReportSection report={r.report} keys={['price_move']} />
      </Section>
      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="시장지수 대비 초과수익률">
          <ExcessReturnChart charts={r.charts} />
          <div className="text-[11px] text-slate-500">{r.charts.market_index?.label} 대비 누적 상대수익률(%p). 공식 KRX 지수가 아닌 대용치입니다(지수 API 이용신청 승인 후 교체 가능).</div>
        </Section>
        <Section title="주가 변동 원인 분해 (시장·산업·개별)">
          <AttributionTable a={t?.attribution} />
        </Section>
      </div>

      {/* ---------------- 동종업계 */}
      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="기업별 시가총액 비교"><MarketCapChart charts={r.charts} /></Section>
        <Section title="밸류에이션 배수 비교 (정상화·조정 기준)">
          <div className="grid gap-3 md:grid-cols-2">
            <MultiplesChart charts={r.charts} metric="ev_ebit_norm" label="EV/정상EBIT (12분기 평균)" />
            <MultiplesChart charts={r.charts} metric="pe_norm" label="정상 P/E" />
            <MultiplesChart charts={r.charts} metric="per_adj" label="조정 PER (경상순이익 TTM)" />
            <MultiplesChart charts={r.charts} metric="pbr" label="PBR" />
          </div>
        </Section>
      </div>
      <Section title="동종업계 비교표 — 기업을 클릭하면 상세 분석">
        <PeerTable r={r} projectId={projectId} />
        <ReportSection report={r.report} keys={['peers']} />
      </Section>

      {/* ---------------- 실적 */}
      {t && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Section title={`${t.name} 분기 매출·영업이익`}>
            <QuarterlyChart rows={t.metrics.quarters} />
            <div className="text-[11px] text-slate-500">OpenDART 정기보고서. 4분기 = 연간 − 3분기 누적.</div>
          </Section>
          <Section title="영업이익률 추이 (분기)">
            <MarginChart series={[t, ...valPeers].filter((c) => c.metrics.quarters?.length).map((c) => ({ name: c.name, color: colors[c.company_id], rows: c.metrics.quarters.slice(-8), target: c.is_target }))} />
          </Section>
        </div>
      )}

      {/* ---------------- 수주 */}
      {t && (
        <Section title="수주잔고 및 신규 수주 (단일판매·공급계약 공시)">
          {!!r.charts.contracts_monthly?.length && <ContractsChart monthly={r.charts.contracts_monthly} backlog={t.data_status?.backlog?.amount} />}
          <ContractsTable v={t} />
        </Section>
      )}

      {/* ---------------- 보고서 */}
      <Section title="근거 기반 AI 분석 보고서" right={r.report && <ReviewBadge report={r.report} />}>
        <ReportSection report={r.report} keys={['overall', 'positive', 'negative', 'peers', 'price_move', 'fair_value', 'conclusion', 'triggers']} />
      </Section>

      {r.charts.history && r.charts.history.length > 0 && (
        <Section title="과거 분석별 적정가치 변화"><HistoryChart history={r.charts.history} /></Section>
      )}

      <Section title="분석 가정 및 데이터 기준">
        <dl className="grid grid-cols-[180px_1fr] gap-y-1 text-xs">
          {Object.entries(r.run.assumptions ?? {}).map(([k, val]) => (
            <Fragment key={k}><dt className="text-slate-500">{k}</dt><dd>{typeof val === 'string' ? val : JSON.stringify(val)}</dd></Fragment>
          ))}
          <dt className="text-slate-500">엔진 버전 / AI</dt><dd>{r.run.model_version} / {r.run.ai_provider} {r.run.ai_model ?? ''}</dd>
          <dt className="text-slate-500">재무 기준 분기</dt><dd>{Object.entries((r.run.data_versions?.financials ?? {}) as Record<string, string | null>).map(([n, q]) => `${n} ${q ?? '없음'}`).join(' · ')}</dd>
        </dl>
      </Section>
    </div>
  )
}

function ReviewBadge({ report }: { report: Report }) {
  const rv = report.review
  return (
    <div className="text-right text-[11px] text-slate-500">
      Review: {rv.by_ai ? `AI 검증(${rv.model})` : '결정적 검증만'} · 문단 {rv.paragraphs}개 중 <b className={rv.unverified ? 'text-amber-700' : 'text-emerald-700'}>검증 실패 {rv.unverified}개</b>
      {rv.overall_comment && <div className="max-w-md">{rv.overall_comment}</div>}
      {rv.errors.map((e, i) => <div key={i} className="text-amber-700">{e}</div>)}
    </div>
  )
}

function PeerTable({ r, projectId }: { r: Results; projectId: string }) {
  const rows = [...r.companies].sort((a, b) => Number(b.is_target) - Number(a.is_target))
  const val = (c: ValuationResult, k: string) => c.metrics.values?.[k] as number | null | undefined
  return (
    <div className="overflow-x-auto">
      <table className="tabular w-full text-xs">
        <thead className="text-left text-[11px] text-slate-500">
          <tr><th className="py-1">기업</th><th>구분</th><th className="text-right">시가총액</th><th className="text-right">매출(TTM)</th><th className="text-right">영업이익(TTM)</th><th className="text-right">OPM</th><th className="text-right">매출성장</th><th className="text-right">PER(보고)</th><th className="text-right">조정 PER</th><th className="text-right">EV/정상EBIT</th><th className="text-right">정상 P/E</th><th className="text-right">PBR</th><th className="text-right">ROE</th><th className="text-right">1Y 수익률</th><th>평가</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {rows.map((c) => (
            <tr key={c.company_id} className={c.is_target ? 'bg-blue-50/60 dark:bg-blue-950/30' : ''}>
              <td className="py-1.5 font-medium">
                <Link to={`/projects/${projectId}/results/company/${c.company_id}?run=${r.run.id}`} className="hover:text-blue-600 hover:underline">{c.name}</Link>
                {c.is_target && <Badge tone="blue">고객사</Badge>}
              </td>
              <td className="text-slate-500">{c.is_target ? '—' : `${c.category}. ${CATEGORY_LABELS[c.category as 'A']?.slice(0, 6) ?? ''} · ${USE_TYPE_LABELS[c.use_type as 'valuation'] ?? ''}`}</td>
              <td className="text-right">{krw(val(c, 'market_cap'))}</td>
              <td className="text-right">{krw(val(c, 'revenue_ttm'))}</td>
              <td className="text-right">{krw(val(c, 'operating_income_ttm'))}</td>
              <td className="text-right">{pct(val(c, 'op_margin'))}</td>
              <td className="text-right">{pct(val(c, 'revenue_growth'))}</td>
              <td className="text-right" title={c.metrics.na?.per}>{mult(val(c, 'per'))}</td>
              <td className="text-right" title={c.metrics.na?.per_adj}>{mult(val(c, 'per_adj'))}</td>
              <td className="text-right" title={c.metrics.na?.ev_ebit_norm}>{mult(val(c, 'ev_ebit_norm'))}</td>
              <td className="text-right" title={c.metrics.na?.pe_norm}>{mult(val(c, 'pe_norm'))}</td>
              <td className="text-right">{mult(val(c, 'pbr'))}</td>
              <td className="text-right">{pct(val(c, 'roe'))}</td>
              <td className="text-right">{pct(val(c, 'ret_1Y'))}</td>
              <td>{c.verdict ? <Badge tone={VERDICT_TONE[c.verdict] ?? 'slate'}>{c.verdict}</Badge> : <span className="text-slate-400">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 text-[11px] text-slate-500">N/A 위에 마우스를 올리면 사유가 표시됩니다(적자 기업의 음수 PER은 비교하지 않음). 비상장·해외 기업은 시장 데이터가 없어 기술 경쟁 분석용으로만 표시됩니다.</div>
    </div>
  )
}

// ================================================================ company detail
export function CompanyDetail() {
  const { project } = useProject()
  const { companyId = '' } = useParams()
  const runId = useLatestRun()
  const q = useQuery({ queryKey: ['company-result', runId, companyId], queryFn: () => analysisApi.company(project.id, runId!, Number(companyId)), enabled: !!runId })
  const all = useQuery({ queryKey: ['results', project.id, runId], queryFn: () => analysisApi.results(project.id, runId!), enabled: !!runId })
  if (!runId) return <Empty title="분석 결과가 없습니다" />
  if (q.isLoading || all.isLoading) return <Spinner />
  if (!q.data || !all.data) return <Notice tone="red">{(q.error as Error)?.message}</Notice>
  const v = q.data.valuation
  const tg = q.data.target
  const vals = v.metrics.values
  const n = (k: string) => vals?.[k] as number | null | undefined
  const tn = (k: string) => tg?.metrics.values?.[k] as number | null | undefined
  return (
    <EvidenceProvider projectId={project.id} runId={runId}>
      <div className="space-y-4">
        <Link to={`/projects/${project.id}/results?run=${runId}`} className="text-xs text-blue-600 hover:underline">← 종합 결과로</Link>
        <Card className="p-5">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-bold">{v.name}</h2>
            <span className="tabular text-sm text-slate-500">{v.stock_code ?? '—'} · {v.market}</span>
            {v.is_target ? <Badge tone="blue">고객사</Badge> : <Badge>{v.category}. {CATEGORY_LABELS[v.category as 'A']} · {USE_TYPE_LABELS[v.use_type as 'valuation']}</Badge>}
            {v.verdict && <Badge tone={VERDICT_TONE[v.verdict] ?? 'slate'}>{v.verdict} · 신뢰도 {v.confidence?.toFixed(0)}</Badge>}
          </div>
          <div className="mt-1 text-xs text-slate-500">대표 {q.data.company.ceo ?? '—'} · 업종코드 {q.data.company.induty_code ?? '—'} · {q.data.company.verification_note}</div>
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <Kpi label="주가" value={won(n('price'))} sub={String(vals.price_date ?? '')} eids={[v.metrics.eids.price]} />
            <Kpi label="시가총액" value={krw(n('market_cap'))} eids={[v.metrics.eids.market_cap]} />
            <Kpi label="매출(TTM)" value={krw(n('revenue_ttm'))} eids={[v.metrics.eids.revenue_ttm]} />
            <Kpi label="영업이익(TTM)" value={krw(n('operating_income_ttm'))} eids={[v.metrics.eids.operating_income_ttm]} />
            <Kpi label="PER / EV/EBIT" value={`${mult(n('per'))} / ${mult(n('ev_ebit'))}`} sub={v.metrics.na.per ?? v.metrics.na.ev_ebit} />
            <Kpi label="수익률 1M / 3M / 1Y" value={`${pct(n('ret_1M'))} / ${pct(n('ret_3M'))} / ${pct(n('ret_1Y'))}`} />
          </div>
        </Card>
        {!v.is_target && tg && (
          <Section title={`기준 고객사(${tg.name})와의 비교`}>
            <table className="tabular w-full text-xs">
              <thead className="text-left text-[11px] text-slate-500"><tr><th className="py-1">지표</th><th className="text-right">{v.name}</th><th className="text-right">{tg.name}</th></tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {[['매출(TTM)', 'revenue_ttm', krw], ['영업이익률', 'op_margin', pct], ['매출 성장률', 'revenue_growth', pct], ['ROE', 'roe', pct], ['PER', 'per', mult], ['PBR', 'pbr', mult], ['EV/EBIT', 'ev_ebit', mult], ['1Y 수익률', 'ret_1Y', pct]].map(([l, k, f]) => (
                  <tr key={k as string}><td className="py-1">{l as string}</td><td className="text-right">{(f as (x?: number | null) => string)(n(k as string))}</td><td className="text-right">{(f as (x?: number | null) => string)(tn(k as string))}</td></tr>
                ))}
              </tbody>
            </table>
          </Section>
        )}
        <div className="grid gap-4 xl:grid-cols-2">
          <Section title="최근 주가 동향 (기준=100)">
            <PriceCompareChart charts={all.data.charts} onlyIds={[v.company_id, ...(tg && tg.company_id !== v.company_id ? [tg.company_id] : [])]} />
          </Section>
          <Section title="실적 변화 (분기)">{v.metrics.quarters.length ? <QuarterlyChart rows={v.metrics.quarters} /> : <div className="text-sm text-slate-500">DART 재무 데이터 없음</div>}</Section>
        </div>
        <Section title="시장 대비 상대수익률 · 주가 변동 분해"><AttributionTable a={v.attribution} /></Section>
        {v.methods && Object.keys(v.methods).length > 0 && (
          <Section title="적정가치 분석 (같은 계산 엔진)">
            <div className="grid gap-2 md:grid-cols-3"><MethodCard m={v.methods.forward} /><MethodCard m={v.methods.peer} /><MethodCard m={v.methods.history} /></div>
            {v.fair_value?.scenarios && <div className="mt-2 text-sm">가중 결합 적정주가: <EvidenceLink ids={[v.fair_value.eid]}>{won(v.fair_value.scenarios.conservative)} ~ {won(v.fair_value.scenarios.optimistic)} (기준 {won(v.fair_value.scenarios.base)})</EvidenceLink></div>}
            {!!v.confidence_breakdown?.length && <div className="mt-1 text-[11px] text-slate-500">평가 근거·신뢰도 감점: {v.confidence_breakdown.map((b) => `${b.reason} (${b.points})${b.recover ? ` → 회복: ${b.recover}` : ''}`).join(' · ')}</div>}
            <EarningsAdjustment v={v} />
          </Section>
        )}
        <Section title="수주 현황"><ContractsTable v={v} /></Section>
        <Section title="데이터 없음·산출 불가 항목">
          <ul className="text-xs text-slate-600 dark:text-slate-400">{Object.entries(v.metrics.na ?? {}).map(([k, why]) => <li key={k}>· {k}: {why}</li>)}</ul>
        </Section>
      </div>
    </EvidenceProvider>
  )
}
