// 보고용 리포트 (/print/projects/:projectId/runs/:runId/brief) — 4쪽 내외의 보고서 양식.
// 상세 분석 PDF와 같은 결과·같은 검증 통과 서술만 사용한다(새 AI 호출 없음, 검증 미통과 문단은 싣지 않음).
//  1쪽: 결론 스냅샷(판단·적정가·핵심 데이터·투자 포인트·리스크·연간 실적)
//  2쪽: 밸류에이션 (방법별 결과, 비교기업)
//  3쪽: 실적·수주와 주가 동향
//  4쪽: 주요 이슈, 결론 및 체크포인트, 고지
import { useQuery } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { analysisApi, krw, METHOD_LABEL, pct, won, type IssueRow, type Results, type ValuationResult } from '../apiAnalysis'
import { FairValueChart, PriceCompareChart, QuarterlyChart } from '../charts'
import { fmtDate } from '../ui'
import { EvidenceProvider } from './Evidence'
import { MethodTable, PeerCompactTable, splitParas, Td, Th } from './PrintReport'

type Para = NonNullable<Results['report']>['content']['sections'][number]['paragraphs'][number]

/** 요점: 문단의 주제문 + 뒷받침 문장(검증 통과 문장 그대로, 최대 2문장·약 170자). */
function lead(p: Para): string {
  // 문장 끝(…다. / …요. / …음.)에서만 나눈다 — '1.9배' 같은 소수점은 나누지 않음
  const sents = p.text.split(/(?<=(?:다|요|음)\.)\s+/).filter(Boolean)
  let out = sents[0].trim()
  if (sents[1] && out.length + sents[1].length <= 170) out += ' ' + sents[1].trim()
  return out
}

function H({ no, children }: { no?: string; children: ReactNode }) {
  return (
    <h2 className="mb-2 mt-5 flex items-baseline gap-2 border-b-2 border-slate-900 pb-1 text-[14px] font-bold first:mt-0">
      {no && <span className="text-blue-800">{no}</span>}{children}
    </h2>
  )
}
function Fig({ title, source, children }: { title: string; source: string; children: ReactNode }) {
  return (
    <figure className="pr-keep my-2">
      <figcaption className="mb-1 text-[9.5px] font-semibold text-slate-700">{title}</figcaption>
      {children}
      <div className="text-right text-[8px] text-slate-500">자료: {source}</div>
    </figure>
  )
}
function Body({ paras }: { paras?: Para[] }) {
  if (!paras?.length) return null
  return <div className="space-y-1.5">{paras.map((p) => <p key={p.ref} className="text-justify text-[10.5px] leading-[1.75]">{p.text}</p>)}</div>
}
function Bullets({ items, tone }: { items: string[]; tone: 'up' | 'down' }) {
  if (!items.length) return <div className="text-[10px] text-slate-500">검증을 통과한 항목이 없습니다.</div>
  return (
    <ul className="space-y-1">
      {items.map((x, i) => (
        <li key={i} className="flex gap-1.5 text-[10px] leading-snug">
          <span className={tone === 'up' ? 'text-emerald-700' : 'text-red-700'}>{tone === 'up' ? '▲' : '▼'}</span><span>{x}</span>
        </li>
      ))}
    </ul>
  )
}

function AnnualTable({ t }: { t: ValuationResult }) {
  const rev = t.metrics.annual?.revenue ?? {}
  const op = t.metrics.annual?.operating_income ?? {}
  const years = Object.keys(rev).sort().slice(-3)
  const v = t.metrics.values as Record<string, number | null | undefined>
  const cols = [...years.map((y) => ({ k: `${y}`, rev: rev[y], op: op[y] })), { k: 'TTM', rev: v.revenue_ttm ?? undefined, op: v.operating_income_ttm ?? undefined }]
  return (
    <table className="w-full text-[9.5px]">
      <thead><tr><Th>(억원)</Th>{cols.map((c) => <Th key={c.k} right>{c.k === 'TTM' ? 'TTM' : `${c.k}A`}</Th>)}</tr></thead>
      <tbody>
        <tr><Td className="text-slate-500">매출액</Td>{cols.map((c) => <Td key={c.k} right>{c.rev != null ? Math.round(c.rev / 1e8).toLocaleString() : '—'}</Td>)}</tr>
        <tr><Td className="text-slate-500">영업이익</Td>{cols.map((c) => <Td key={c.k} right>{c.op != null ? Math.round(c.op / 1e8).toLocaleString() : '—'}</Td>)}</tr>
        <tr><Td className="text-slate-500">영업이익률</Td>{cols.map((c) => <Td key={c.k} right>{c.rev && c.op != null ? pct(c.op / c.rev) : '—'}</Td>)}</tr>
      </tbody>
    </table>
  )
}

function IssueBrief({ issues }: { issues?: IssueRow[] }) {
  const rows = (issues ?? []).filter((r) => r.type === 'news' || /단일판매|공급계약|잠정|유상증자|전환사채|최대주주|자기주식|시설투자/.test(r.title))
  const pick = [...rows.filter((r) => r.role === 'target' && r.type === 'news'), ...rows.filter((r) => r.role === 'theme'),
    ...rows.filter((r) => r.role === 'peer' && r.type === 'news'), ...rows.filter((r) => r.role === 'target' && r.type === 'disclosure')].slice(0, 12)
  if (!pick.length) return <div className="text-[10px] text-slate-500">최근 주요 이슈가 없습니다.</div>
  const VER: Record<string, string> = { confirmed: '공시 확인', nearby: '인근 공시', news_only: '보도' }
  return (
    <table className="w-full text-[9.5px]">
      <thead><tr><Th>일자</Th><Th>대상</Th><Th>내용</Th><Th>구분</Th></tr></thead>
      <tbody>
        {pick.sort((a, b) => b.date.localeCompare(a.date)).map((r, i) => (
          <tr key={i} className="align-top">
            <Td className="tabular whitespace-nowrap">{r.date}{r.type === 'news' && r.date_certain === false ? '*' : ''}</Td>
            <Td className="whitespace-nowrap">{r.company}</Td>
            <Td>{r.title}{r.type === 'news' && r.outlets?.length ? <span className="text-slate-500"> ({r.outlets.slice(0, 2).join(', ')})</span> : null}</Td>
            <Td className="whitespace-nowrap text-slate-600">{r.type === 'disclosure' ? '공시' : VER[r.verification ?? 'news_only']}</Td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function BriefReport() {
  const { projectId = '', runId = '' } = useParams()
  const [sp] = useSearchParams()
  const q = useQuery({ queryKey: ['results', projectId, runId], queryFn: () => analysisApi.results(projectId, runId) })
  useEffect(() => {
    if (!q.data) return
    document.title = `${q.data.target?.name ?? 'report'}_분석리포트`  // 브라우저 인쇄 저장 시 기본 파일명
    document.documentElement.dataset.reportReady = '1'
    if (sp.get('autoprint') === '1') {
      const h = setTimeout(() => window.print(), 800)
      return () => clearTimeout(h)
    }
  }, [q.data, sp])

  if (q.isLoading) return <div className="p-8 text-sm text-slate-500">보고서 불러오는 중…</div>
  if (q.isError || !q.data) return <div className="p-8 text-sm text-red-600">{(q.error as Error)?.message ?? '결과를 불러오지 못했습니다.'}</div>
  const r = q.data
  const t = r.target
  const s = r.run.summary
  const fv = t?.fair_value
  const V = splitParas(r).verified
  const verdict = s.verdict ?? '판단 유보'
  const tone = { 저평가: 'bg-emerald-700', 고평가: 'bg-red-700', 적정: 'bg-blue-800' }[verdict] ?? 'bg-slate-600'
  const vals = (t?.metrics.values ?? {}) as Record<string, number | null | undefined>
  const ups = (V.positive ?? []).map(lead).slice(0, 4)
  const downs = (V.negative ?? []).map(lead).slice(0, 4)
  const backlog = t?.data_status?.backlog
  const sched = t?.data_status?.schedule
  const att = t?.attribution
  const day = fmtDate(r.run.finished_at)

  return (
    <EvidenceProvider projectId={projectId} runId={runId}>
      <div className="print-report brief bg-white text-[10.5px] leading-relaxed text-slate-900">
        {/* ============ 1쪽: 결론 스냅샷 */}
        <section>
          <div className="flex items-end justify-between border-b-[3px] border-slate-900 pb-2">
            <div>
              <div className="text-[9px] font-semibold uppercase tracking-[0.2em] text-blue-800">Company Report</div>
              <h1 className="text-[28px] font-bold leading-tight">{t?.name} <span className="text-[13px] font-normal text-slate-500">({t?.stock_code} · {t?.market})</span></h1>
            </div>
            <div className="text-right text-[9px] text-slate-500">
              <div>{day} · 주가 기준일 {fmtDate(r.run.price_as_of)}</div>
              <div>ValueScope 기업분석 보고서</div>
            </div>
          </div>

          {r.report?.content.summary && (
            <p className="mt-3 text-justify text-[12px] font-medium leading-[1.7] text-slate-800">{r.report.content.summary}</p>
          )}

          <div className="mt-4 grid grid-cols-[190px_1fr] gap-5">
            {/* 왼쪽: 판단과 핵심 데이터 */}
            <aside className="space-y-3">
              <div className={`${tone} rounded px-3 py-2 text-white`}>
                <div className="text-[9px] opacity-80">종합 판단</div>
                <div className="text-[22px] font-bold leading-tight">{verdict}</div>
                <div className="text-[9.5px] opacity-90">분석 신뢰도 {s.confidence?.toFixed(0) ?? '—'}/100</div>
              </div>
              <table className="w-full text-[9.5px]">
                <tbody>
                  {[
                    ['현재 주가', won(s.price)],
                    ['적정주가(기준)', won(fv?.scenarios?.base)],
                    ['적정가 범위', fv?.scenarios ? `${won(fv.scenarios.conservative)}~${won(fv.scenarios.optimistic)}` : '—'],
                    ['괴리율(기준)', pct(s.upside_base)],
                    ['시가총액', krw(s.market_cap)],
                    ['순현금', vals.net_debt != null ? krw(-vals.net_debt) : '—'],
                    ['수주잔고', backlog?.amount != null ? krw(backlog.amount) : '—'],
                    ['PER(조정)', vals.per_adj != null ? `${vals.per_adj.toFixed(1)}배` : '—'],
                    ['PBR', vals.pbr != null ? `${vals.pbr.toFixed(2)}배` : '—'],
                    ['1년 주가 수익률', pct(vals.ret_1Y)],
                  ].map(([k, v]) => <tr key={k}><Td className="text-slate-500">{k}</Td><Td right strong>{v}</Td></tr>)}
                </tbody>
              </table>
              <div className="text-[8px] text-slate-500">비교군: {(s.valuation_peers ?? []).join(', ')}</div>
            </aside>
            {/* 오른쪽: 투자 포인트 · 리스크 · 실적 */}
            <div className="space-y-3">
              <div>
                <div className="mb-1 text-[11px] font-bold text-emerald-800">투자 포인트</div>
                <Bullets items={ups} tone="up" />
              </div>
              <div>
                <div className="mb-1 text-[11px] font-bold text-red-800">리스크</div>
                <Bullets items={downs} tone="down" />
              </div>
              {t && <Fig title="연간 실적 요약" source="OpenDART"><AnnualTable t={t} /></Fig>}
            </div>
          </div>
          {r.charts.scenarios && (
            <Fig title="현재 주가와 평가 방법별 적정가치 범위" source="ValueScope">
              <FairValueChart scenarios={r.charts.scenarios} price={s.price} />
            </Fig>
          )}
        </section>

        {/* ============ 2쪽: 밸류에이션 */}
        <section className="pr-page-break">
          <H no="1.">밸류에이션</H>
          <Body paras={V.fair_value} />
          {t && <Fig title="평가 방법별 주당가치와 가중 결합" source="ValueScope"><MethodTable t={t} /></Fig>}
          <div className="text-[9px] text-slate-500">
            가중치: {Object.entries(fv?.weights ?? {}).map(([k, w]) => `${METHOD_LABEL[k]} ${pct(w, 0)}`).join(', ')} · 신뢰도가 낮은 방법은 가중치를 절반으로 줄여 반영
          </div>
          <H no="2.">동종업계 비교</H>
          <Body paras={(V.peers ?? []).slice(0, 2)} />
          <Fig title="비교 기업 주요 지표" source="KRX, OpenDART, ValueScope"><PeerCompactTable r={r} /></Fig>
        </section>

        {/* ============ 3쪽: 실적·수주, 주가 */}
        <section className="pr-page-break">
          <H no="3.">실적과 수주</H>
          <Body paras={(V.overall ?? []).slice(1, 2)} />
          {t && <Fig title="분기 매출·영업이익" source="OpenDART 정기보고서"><QuarterlyChart rows={t.metrics.quarters} /></Fig>}
          {sched?.available && (
            <Fig title="수주잔고와 향후 12개월 납기 도래 매출(추정)" source="OpenDART 정기보고서·수주공시, ValueScope">
              <table className="w-full text-[9.5px]">
                <tbody>
                  <tr><Td className="text-slate-500">공식 수주잔고 ({sched.as_of})</Td><Td right strong>{krw(sched.official_backlog)}</Td>
                    <Td className="pl-4 text-slate-500">향후 12개월 납기 도래(기준)</Td><Td right strong>{krw(sched.scenarios?.base.revenue)}</Td></tr>
                  <tr><Td className="text-slate-500">보수 / 낙관</Td><Td right>{krw(sched.scenarios?.conservative.revenue)} / {krw(sched.scenarios?.optimistic.revenue)}</Td>
                    <Td className="pl-4 text-slate-500">금액 비공개 계약</Td><Td right>{sched.undisclosed_contracts}건 · {krw(sched.undisclosed_pool)}</Td></tr>
                </tbody>
              </table>
            </Fig>
          )}
          <H no="4.">주가 동향</H>
          <Body paras={(V.price_move ?? []).slice(0, 1)} />
          <Fig title="고객사와 비교기업 주가 (최근 1년, 기준=100)" source="KRX"><PriceCompareChart charts={r.charts} /></Fig>
          {att?.available && (
            <Fig title="주가 변동 원인 분해" source="KRX, ValueScope 회귀분석">
              <table className="w-full text-[9.5px]">
                <thead><tr><Th>기간</Th><Th right>합계</Th><Th right>시장</Th><Th right>산업</Th><Th right>개별·미확인</Th></tr></thead>
                <tbody>{Object.entries(att.windows ?? {}).map(([k, w]) => (
                  <tr key={k}><Td>{k}</Td><Td right strong>{pct(w.total)}</Td><Td right>{pct(w.market)}</Td><Td right>{pct(w.industry)}</Td><Td right>{pct(w.individual)}</Td></tr>
                ))}</tbody>
              </table>
            </Fig>
          )}
        </section>

        {/* ============ 4쪽: 이슈, 결론 */}
        <section className="pr-page-break">
          <H no="5.">주요 이슈와 업황</H>
          <Body paras={(V.issues ?? []).slice(0, 2)} />
          <Fig title="최근 주요 이슈 (공시·보도)" source="OpenDART, 언론 보도(Claude 웹검색)"><IssueBrief issues={r.charts.issues} /></Fig>
          <div className="text-[8px] text-slate-500">* 보도일 추정. '보도'는 공시로 확인되지 않은 언론 보도로, 사실로 단정하지 않습니다.</div>
          <H no="6.">결론 및 체크포인트</H>
          <Body paras={V.conclusion} />
          {!!V.triggers?.length && (
            <div className="pr-keep mt-2 rounded border border-slate-300 p-2">
              <div className="mb-1 text-[10.5px] font-bold">평가가 바뀔 수 있는 조건</div>
              <ul className="list-disc space-y-1 pl-4 text-[10px] leading-snug">{V.triggers.map((p) => <li key={p.ref}>{p.text}</li>)}</ul>
            </div>
          )}
          <div className="pr-keep mt-6 border-t border-slate-300 pt-2 text-[8.5px] leading-relaxed text-slate-500">
            본 보고서는 OpenDART 공시·KRX 시세 등 공개 데이터와 언론 보도(보조 근거)를 바탕으로 ValueScope가 산출한 분석 자료이며, 투자 권유나 매매 추천이 아닙니다.
            적정가치와 향후 실적은 명시된 가정에 따른 추정치로 실제와 다를 수 있습니다. 본문 서술은 근거 대조 검증을 통과한 내용만 실었으며,
            수치별 근거·산출 방법·검증 내역은 '상세 분석 보고서(PDF)'에 있습니다.
            {r.report && <> 작성 {r.report.ai_model ?? '규칙 기반'} · 검증 {r.report.review.by_ai ? `Review Agent(${r.report.review.model})` : '수치 대조'}.</>}
          </div>
        </section>
      </div>
    </EvidenceProvider>
  )
}
