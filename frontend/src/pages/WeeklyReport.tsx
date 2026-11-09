// 주간 주가 리포트 (/print/projects/:projectId/weekly/:weeklyId) — A4 세로 2~3쪽 보고서 양식.
//  1쪽: 주간 결론(판정·핵심 수치) + 주가 경로 + 요약·동조 여부
//  2쪽: 일별 실제 vs 기대 vs 개별 요인 + 일별 상세(공시·보도) + 개별 요인 해석
//  3쪽: 비교기업별 움직임 · 변동성 · 이번 주 공시·보도 · 다음 주 확인할 점 · 분석 방법 · 고지
// 검증을 통과한 해석 문단만 싣는다(미통과 문단은 제외).
import { useQuery } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { addDays, weeklyApi, type WeeklyReport as Rep, type WeeklyResult } from '../apiWeekly'
import { WeeklyDailyChart, WeeklyPathChart } from '../charts'
import { fmtDateTime } from '../ui'
import { Td, Th } from './PrintReport'

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}%`)
const pp = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%p`)
const TONE: Record<string, string> = { comove: 'bg-blue-800', idiosyncratic: 'bg-red-700', mixed: 'bg-amber-600' }

function H({ no, children }: { no: string; children: ReactNode }) {
  return <h2 className="mb-2 mt-5 flex items-baseline gap-2 border-b-2 border-slate-900 pb-1 text-[14px] font-bold first:mt-0"><span className="text-violet-800">{no}</span>{children}</h2>
}
function Fig({ title, source, children, note }: { title: string; source: string; children: ReactNode; note?: ReactNode }) {
  return (
    <figure className="pr-keep my-2">
      <figcaption className="mb-1 text-[9.5px] font-semibold text-slate-700">{title}</figcaption>
      {children}
      <div className="mt-0.5 flex justify-between gap-3 text-[8px] text-slate-500"><span>{note}</span><span className="shrink-0">자료: {source}</span></div>
    </figure>
  )
}
function Body({ rep, keyName }: { rep?: Rep; keyName: string }) {
  const sec = rep?.sections.find((s) => s.key === keyName)
  if (!sec?.paragraphs.length) return null
  return <div className="space-y-1.5">{sec.paragraphs.map((p) => <p key={p.ref} className="text-justify text-[10.5px] leading-[1.75]">{p.text}</p>)}</div>
}

export default function WeeklyReport() {
  const { projectId = '', weeklyId = '' } = useParams()
  const [sp] = useSearchParams()
  const q = useQuery({ queryKey: ['weekly-run', projectId, weeklyId], queryFn: () => weeklyApi.get(projectId, weeklyId) })
  useEffect(() => {
    if (!q.data?.result) return
    document.title = `${q.data.company_name ?? 'report'}_주간리포트_${q.data.week_start.replace(/-/g, '')}`  // 브라우저 인쇄 저장 시 기본 파일명
    document.documentElement.dataset.reportReady = '1'
    if (sp.get('autoprint') === '1') {
      const h = setTimeout(() => window.print(), 800)
      return () => clearTimeout(h)
    }
  }, [q.data, sp])

  if (q.isLoading) return <div className="p-8 text-sm text-slate-500">보고서 불러오는 중…</div>
  if (q.isError || !q.data) return <div className="p-8 text-sm text-red-600">{(q.error as Error)?.message ?? '결과를 불러오지 못했습니다.'}</div>
  if (q.data.status !== 'succeeded' || !q.data.result) return <div className="p-8 text-sm text-slate-500">완료된 주간 분석만 출력할 수 있습니다.</div>
  const w = q.data
  const r = w.result as WeeklyResult
  const rep = w.report
  const s = r.summary
  const names = Object.fromEntries(r.peers.map((p) => [String(p.company_id), p.name]))
  const target = w.company_name || '고객사'
  const wkLabel = `${r.week.start.replace(/-/g, '.')}(월) ~ ${addDays(r.week.start, 6).slice(5).replace('-', '.')}(일)`
  const vol = r.volatility
  const events = [...r.disclosures.map((x) => ({ ...x, kind: '공시', title: x.title, extra: '' })),
    ...r.news.map((x) => ({ ...x, kind: '보도', title: x.event, extra: `${x.outlets.join(', ')}${x.verification === 'confirmed' ? ' · 공시로 확인' : ' · 기사로만 확인'}` }))]
    .sort((a, b) => b.date.localeCompare(a.date))

  return (
    <div className="print-report brief bg-white text-[10.5px] leading-relaxed text-slate-900">
      {/* ============ 1쪽 */}
      <section>
        <div className="flex items-end justify-between border-b-[3px] border-slate-900 pb-2">
          <div>
            <div className="text-[9px] font-semibold uppercase tracking-[0.2em] text-violet-800">Weekly Price Report</div>
            <h1 className="text-[26px] font-bold leading-tight">{target} <span className="text-[13px] font-normal text-slate-500">주간 주가 분석</span></h1>
          </div>
          <div className="text-right text-[9px] text-slate-500">
            <div className="text-[11px] font-semibold text-slate-800">{wkLabel}</div>
            <div>거래일 {r.week.trading_days}일 · 기준 {r.week.base_date} 종가 · 비교군 v{w.peer_version_no}</div>
            <div>분석 {fmtDateTime(w.finished_at)}</div>
          </div>
        </div>
        {rep?.headline && <p className="mt-3 text-[12.5px] font-semibold leading-[1.65]">{rep.headline}</p>}

        <div className="mt-3 grid grid-cols-[180px_1fr] gap-5">
          <aside className="space-y-2">
            <div className={`${TONE[s.pattern] ?? 'bg-slate-600'} rounded px-3 py-2 text-white`}>
              <div className="text-[9px] opacity-80">주간 판정</div>
              <div className="text-[14px] font-bold leading-snug">{s.pattern_label}</div>
            </div>
            <div className={`rounded border px-3 py-1.5 text-[10px] ${s.volatility_flags.length ? 'border-red-300 text-red-700' : 'border-slate-300 text-slate-600'}`}>
              변동성: <b>{s.volatility_label}</b>{s.abnormal_days.length ? ` · 이상 변동일 ${s.abnormal_days.map((d) => d.slice(5)).join(', ')}` : ' · 이상 변동일 없음'}
            </div>
            <table className="w-full text-[9.5px]">
              <tbody>
                {[
                  [`${target} 주간`, pct(s.target_return)],
                  ['종가', r.week.base_close && r.week.last_close ? `${r.week.base_close.toLocaleString()} → ${r.week.last_close.toLocaleString()}원` : '—'],
                  ['비교군 가중평균', pct(s.peer_return)],
                  ['시장 대용지수', pct(s.market_return)],
                  ['비교군 대비', pp(s.excess_vs_peers)],
                  ['시장 대비', pp(s.excess_vs_market)],
                  ['개별 요인 합계', `${pct(s.residual_sum)}${s.z_week != null ? ` (z ${s.z_week.toFixed(2)})` : ''}`],
                  ['같은 방향', `${s.same_dir_days}/${s.judged_days}일`],
                ].map(([k, v]) => <tr key={k}><Td className="text-slate-500">{k}</Td><Td right strong>{v}</Td></tr>)}
              </tbody>
            </table>
          </aside>
          <div>
            <Fig title={`그림 1. 주가 경로 — ${target} · 비교군 · 시장 (직전 거래일 = 100)`} source="KRX, ValueScope" note="회색 선 = 개별 비교기업">
              <WeeklyPathChart chart={r.chart} names={names} targetName={target} />
            </Fig>
          </div>
        </div>
        <H no="1.">주간 요약</H>
        <Body rep={rep} keyName="overview" />
        <H no="2.">비교군 동조 여부</H>
        <Body rep={rep} keyName="comovement" />
      </section>

      {/* ============ 2쪽 */}
      <section className="pr-page-break">
        <H no="3.">개별 요인과 이벤트</H>
        <Body rep={rep} keyName="idiosyncratic" />
        <Fig title="그림 2. 일별 실제 수익률 vs 기대(시장+비교군) vs 개별 요인" source="KRX, ValueScope 회귀분석"
          note={`빨간 막대 = 이상 변동(|z| ≥ ${r.thresholds.z_day})`}>
          <WeeklyDailyChart days={r.days} />
        </Fig>
        <Fig title="표 1. 일별 상세 — 개별 변동과 공시·보도(당일~2일 전)" source="KRX, OpenDART, 언론 보도"
          note="거래량 = 직전 20거래일 평균 대비 · ↔ = 비교군과 반대 방향 · 시간적 근접은 인과관계를 뜻하지 않음">
          <table className="w-full text-[9px]">
            <thead><tr><Th>날짜</Th><Th right>{target}</Th><Th right>비교군</Th><Th right>시장</Th><Th right>기대</Th><Th right>개별</Th><Th right>z</Th><Th right>거래량</Th><Th>공시·보도</Th></tr></thead>
            <tbody>
              {r.days.map((d) => (
                <tr key={d.date} className={`align-top ${d.abnormal ? 'bg-red-50' : ''}`}>
                  <Td className="whitespace-nowrap">{d.date.slice(5)}({d.weekday}){d.abnormal ? ' ●' : ''}</Td>
                  <Td right strong>{pct(d.ret, 2)}</Td>
                  <Td right>{pct(d.peer_avg, 2)}{d.same_dir === false ? ' ↔' : ''}</Td>
                  <Td right>{pct(d.market, 2)}</Td>
                  <Td right className="text-slate-500">{pct(d.expected, 2)}</Td>
                  <Td right strong>{pct(d.residual, 2)}</Td>
                  <Td right>{d.z != null ? d.z.toFixed(1) : '—'}</Td>
                  <Td right>{d.volume_ratio != null ? `${d.volume_ratio.toFixed(1)}배` : '—'}</Td>
                  <Td>
                    {d.disclosures?.map((x) => <div key={x.url}>[공시] {x.title}</div>)}
                    {d.news?.map((x) => <div key={x.url}>[보도] {x.event}</div>)}
                    {d.abnormal && <div className="text-slate-500">{d.explanation}</div>}
                    {!d.disclosures?.length && !d.news?.length && !d.abnormal && '—'}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Fig>
        <H no="4.">변동성 · 거래량</H>
        <Body rep={rep} keyName="volatility" />
        <Fig title="표 2. 변동성·거래량 (직전 대비)" source="KRX 일별매매정보"
          note={`변동성 ${r.thresholds.vol_ratio}배 · 변동폭 ${r.thresholds.range_ratio}배 · 거래량 ${r.thresholds.volume_ratio}배 이상이면 '변동성 확대'`}>
          <table className="w-full text-[9.5px]">
            <thead><tr><Th>항목</Th><Th right>이번 주</Th><Th right>직전 평균</Th><Th right>배수</Th></tr></thead>
            <tbody>
              <tr><Td>일별 수익률 변동성</Td><Td right>{vol.ret_std_week != null ? `${(vol.ret_std_week * 100).toFixed(2)}%` : '—'}</Td><Td right>{vol.ret_std_60d != null ? `${(vol.ret_std_60d * 100).toFixed(2)}% (60일)` : '—'}</Td><Td right strong>{vol.std_ratio != null ? `${vol.std_ratio.toFixed(1)}배` : '—'}</Td></tr>
              <tr><Td>평균 일중 변동폭(고가−저가)</Td><Td right>{vol.range_week != null ? `${(vol.range_week * 100).toFixed(2)}%` : '—'}</Td><Td right>{vol.range_60d != null ? `${(vol.range_60d * 100).toFixed(2)}% (60일)` : '—'}</Td><Td right strong>{vol.range_ratio != null ? `${vol.range_ratio.toFixed(1)}배` : '—'}</Td></tr>
              <tr><Td>평균 거래량</Td><Td right>{vol.volume_week != null ? Math.round(vol.volume_week).toLocaleString() : '—'}</Td><Td right>{vol.volume_20d != null ? `${Math.round(vol.volume_20d).toLocaleString()} (20일)` : '—'}</Td><Td right strong>{vol.volume_ratio != null ? `${vol.volume_ratio.toFixed(1)}배` : '—'}</Td></tr>
            </tbody>
          </table>
        </Fig>
      </section>

      {/* ============ 3쪽 */}
      <section className="pr-page-break">
        <H no="5.">비교기업별 움직임</H>
        <Fig title="표 3. 비교기업 주간 수익률과 동조성" source="KRX, ValueScope" note="가중치 = STEP 1 적합성 점수 × 활용 구분 · 같은 방향 = 고객사와 일별 부호 일치 일수">
          <table className="w-full text-[9.5px]">
            <thead><tr><Th>기업</Th><Th right>가중치</Th><Th right>주간</Th><Th right>고객사 대비</Th><Th right>같은 방향</Th><Th right>60일 상관</Th></tr></thead>
            <tbody>
              <tr className="bg-blue-50 font-semibold"><Td>{target} (고객사)</Td><Td right>—</Td><Td right>{pct(s.target_return)}</Td><Td right>—</Td><Td right>—</Td><Td right>—</Td></tr>
              {[...r.peers].sort((a, b) => (b.week_return ?? -9) - (a.week_return ?? -9)).map((p) => (
                <tr key={p.company_id}>
                  <Td>{p.name}</Td><Td right>{p.weight.toFixed(2)}</Td><Td right>{p.available ? pct(p.week_return) : '시세 없음'}</Td>
                  <Td right>{p.available && p.week_return != null ? pp(s.target_return - p.week_return) : '—'}</Td>
                  <Td right>{p.available ? `${p.same_dir_days}/${r.week.trading_days}일` : '—'}</Td>
                  <Td right>{p.corr_60d != null ? p.corr_60d.toFixed(2) : '—'}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Fig>
        <H no="6.">이번 주 공시·보도</H>
        {events.length ? (
          <Fig title="표 4. 고객사·비교기업 공시와 보도" source={r.news_enabled ? 'OpenDART, 언론 보도(Claude 웹검색, 링크 확인)' : 'OpenDART (뉴스 검색 제외)'} note="* 보도일 추정">
            <table className="w-full text-[9.5px]">
              <thead><tr><Th>일자</Th><Th>기업</Th><Th>구분</Th><Th>내용</Th></tr></thead>
              <tbody>{events.map((x, i) => (
                <tr key={i} className="align-top"><Td className="whitespace-nowrap">{x.date.slice(5)}{x.kind === '보도' && 'date_certain' in x && !x.date_certain ? '*' : ''}</Td>
                  <Td className="whitespace-nowrap">{x.company}</Td><Td>{x.kind}</Td><Td>{x.title}{x.extra && <span className="text-slate-500"> ({x.extra})</span>}</Td></tr>
              ))}</tbody>
            </table>
          </Fig>
        ) : <p className="text-slate-500">해당 주 공시·보도가 없습니다.</p>}
        <H no="7.">다음 주 확인할 점</H>
        <Body rep={rep} keyName="watch" />

        <div className="pr-keep mt-5 rounded border border-slate-300 p-2 text-[8.5px] leading-relaxed text-slate-600">
          <b>분석 방법</b> — 기대수익률 = α + β시장({r.model.beta_market.toFixed(2)}) × {r.model.market_label} + β산업({(r.model.beta_industry ?? 0).toFixed(2)}) × 비교군 가중평균 중 시장으로 설명되지 않는 부분.
          해당 주 이전 {r.model.n}거래일({r.model.period.join('~')})로 추정(R² {r.model.r2.toFixed(2)}, 일별 잔차 표준편차 {(r.model.sigma_daily * 100).toFixed(2)}%) — 해당 주 데이터는 모형에 사용하지 않음.
          개별 요인 = 실제 − 기대. 일별 |z| ≥ {r.thresholds.z_day} 이상 변동일, 주간 |z| ≥ {r.thresholds.z_week} 개별 요인 우세, 그 외 비교군과 방향이 같으면 동조(폭 차이 5%p 이상은 함께 표시).
        </div>
        <div className="pr-keep mt-3 border-t border-slate-300 pt-2 text-[8.5px] leading-relaxed text-slate-500">
          본 보고서는 KRX 시세·OpenDART 공시와 언론 보도(보조 근거)를 바탕으로 ValueScope가 산출한 주가 동향 분석이며, 투자 권유나 매매 추천이 아닙니다.
          공시·보도와 주가 변동의 시간적 근접은 인과관계를 뜻하지 않습니다. 시장지수는 KRX 일별매매정보 기반 시가총액가중 대용치(공식 지수 아님)입니다.
          {rep && <> 해석: {rep.status === 'generated' ? `AI ${rep.model ?? ''}` : '규칙 기반'} · {rep.verification}{rep.held.length ? ` · 검증 미통과 ${rep.held.length}개 문단 제외` : ''}.</>}
        </div>
      </section>
    </div>
  )
}
