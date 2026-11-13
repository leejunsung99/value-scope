// 주간 주가 리포트 (/print/projects/:projectId/weekly/:weeklyId) — A4 세로 3쪽, 리포트 문체.
// 통계·모형 용어(z값, 기대수익률, 개별 요인 등)와 분석 방법 설명은 싣지 않는다(화면에서만 확인).
//  1쪽: 이번 주 흐름(판정·핵심 수치) + 주가 경로 + 주간 요약 + 비교기업과의 동조
//  2쪽: 회사 고유 이슈 + 일별 등락률 비교 + 일별 주요 공시·보도 + 변동성·거래량
//  3쪽: 비교기업별 움직임 + 이번 주 공시·보도
import { useQuery } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { addDays, weeklyApi, type WeeklyReport as Rep, type WeeklyResult } from '../apiWeekly'
import { WeeklyCompareChart, WeeklyPathChart } from '../charts'
import { Td, Th } from './PrintReport'

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}%`)
const pp = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%p`)
const md = (iso: string) => `${Number(iso.slice(5, 7))}월 ${Number(iso.slice(8, 10))}일`
const TONE: Record<string, string> = { comove: 'bg-blue-800', idiosyncratic: 'bg-red-700', mixed: 'bg-amber-600' }
/** '비교기업과 동반 하락 (비교기업보다 10.8%p 약세)' → ['비교기업과 동반 하락', '비교기업보다 10.8%p 약세'] */
export function splitLabel(label: string): [string, string | null] {
  const m = /^(.*?)\s*\((.*)\)\s*$/.exec(label)
  return m ? [m[1], m[2]] : [label, null]
}

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
  return <div className="space-y-1.5">{sec.paragraphs.map((p) => <p key={p.ref} className="text-justify text-[10.5px] leading-[1.8]">{p.text}</p>)}</div>
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
  const target = w.company_name || '고객사'
  const names = Object.fromEntries(r.peers.map((p) => [String(p.company_id), p.name]))
  const wkLabel = `${r.week.start.replace(/-/g, '.')}(월) ~ ${addDays(r.week.start, 6).slice(5).replace('-', '.')}(일)`
  const mkt = r.model.market_label.split(' ')[0]
  const vol = r.volatility
  const events = [...r.disclosures.map((x) => ({ date: x.date, company: x.company, kind: '공시', title: x.title, extra: '', estimated: false })),
    ...r.news.map((x) => ({ date: x.date, company: x.company, kind: '보도', title: x.event, estimated: !x.date_certain,
      extra: `${x.outlets.slice(0, 2).join(', ')}${x.verification === 'confirmed' ? ' · 공시로 확인' : ''}` }))]
    .sort((a, b) => b.date.localeCompare(a.date))
  const hasUnusual = r.days.some((d) => d.abnormal)

  return (
    <div className="print-report brief bg-white text-[10.5px] leading-relaxed text-slate-900">
      {/* ============ 1쪽 */}
      <section>
        <div className="flex items-end justify-between border-b-[3px] border-slate-900 pb-2">
          <div>
            <div className="text-[9px] font-semibold uppercase tracking-[0.2em] text-violet-800">Weekly Price Report</div>
            <h1 className="text-[26px] font-bold leading-tight">{target} <span className="text-[13px] font-normal text-slate-500">주간 주가 동향</span></h1>
          </div>
          <div className="text-right text-[9px] text-slate-500">
            <div className="text-[11px] font-semibold text-slate-800">{wkLabel}</div>
            <div>{r.week.trading_days}거래일 · {md(r.week.base_date)} 종가 대비</div>
          </div>
        </div>
        {rep?.headline && <p className="mt-3 text-[12.5px] font-semibold leading-[1.65]">{rep.headline}</p>}

        <div className="mt-3 grid grid-cols-[196px_1fr] gap-5">
          <aside className="space-y-2">
            <div className={`${TONE[s.pattern] ?? 'bg-slate-600'} rounded px-3 py-2 text-white`}>
              <div className="text-[9px] opacity-80">이번 주 흐름</div>
              <div className="text-[14px] font-bold leading-snug">{splitLabel(s.pattern_label)[0]}</div>
              {splitLabel(s.pattern_label)[1] && <div className="mt-0.5 text-[11px] font-semibold opacity-90">{splitLabel(s.pattern_label)[1]}</div>}
            </div>
            <div className={`rounded border px-3 py-1.5 text-[10px] ${s.volatility_flags.length ? 'border-red-300 text-red-700' : 'border-slate-300 text-slate-600'}`}>
              {s.volatility_label}
            </div>
            <table className="w-full text-[9.5px]">
              <tbody>
                {[
                  ['주간 수익률', pct(s.target_return)],
                  ['종가', r.week.base_close && r.week.last_close ? `${r.week.base_close.toLocaleString()}→${r.week.last_close.toLocaleString()}원` : '—'],
                  ['비교기업 평균', pct(s.peer_return)],
                  [`시장(${mkt})`, pct(s.market_return)],
                  ['비교기업 대비', pp(s.excess_vs_peers)],
                  ['시장 대비', pp(s.excess_vs_market)],
                  ['같은 방향(비교기업)', `${s.judged_days}일 중 ${s.same_dir_days}일`],
                ].map(([k, v]) => <tr key={k}><Td className="whitespace-nowrap text-slate-500">{k}</Td><Td right strong className="whitespace-nowrap">{v}</Td></tr>)}
              </tbody>
            </table>
          </aside>
          <Fig title={`그림 1. 주가 흐름 — ${target} · 비교기업 평균 · 시장 (${md(r.week.base_date)} = 100)`} source="KRX" note="회색 선: 개별 비교기업">
            <WeeklyPathChart chart={r.chart} names={names} targetName={target} />
          </Fig>
        </div>
        <H no="1.">주간 요약</H>
        <Body rep={rep} keyName="overview" />
        <H no="2.">비교기업과의 동조</H>
        <Body rep={rep} keyName="comovement" />
      </section>

      {/* ============ 2쪽 */}
      <section className="pr-page-break">
        <H no="3.">{rep?.sections.find((x) => x.key === 'drivers')?.title ?? '비교기업 대비 흐름의 배경'}</H>
        <Body rep={rep} keyName="drivers" />
        {r.context?.target_pre != null && (
          <Fig title={`표 1. 직전 4주 · 이번 주 · 5주 누적 수익률 (${r.context.pre_start ? md(r.context.pre_start) : ''}~${md(r.week.end)})`} source="KRX"
            note="직전 4주에 덜 내린(더 오른) 종목일수록 이번 주 하락폭이 컸는지 함께 보기 위한 표">
            <table className="w-full text-[9.5px]">
              <thead><tr><Th>구분</Th><Th right>직전 4주</Th><Th right>이번 주</Th><Th right>5주 누적</Th></tr></thead>
              <tbody>
                <tr className="bg-blue-50 font-semibold"><Td>{target}</Td><Td right>{pct(r.context.target_pre)}</Td><Td right>{pct(s.target_return)}</Td><Td right>{pct(r.context.target_5w)}</Td></tr>
                <tr className="font-semibold"><Td>비교기업 평균</Td><Td right>{pct(r.context.peer_pre)}</Td><Td right>{pct(s.peer_return)}</Td><Td right>{pct(r.context.peer_5w)}</Td></tr>
                <tr><Td>시장({mkt})</Td><Td right>{pct(r.context.market_pre)}</Td><Td right>{pct(s.market_return)}</Td><Td right>{pct(r.context.market_5w)}</Td></tr>
                {[...r.context.peers].sort((a, b) => b.pre_4w - a.pre_4w).map((p) => (
                  <tr key={p.company_id} className="text-slate-600"><Td className="pl-3">{p.name}</Td><Td right>{pct(p.pre_4w)}</Td><Td right>{pct(p.week)}</Td><Td right>{pct(p.five_week)}</Td></tr>
                ))}
              </tbody>
            </table>
          </Fig>
        )}
        {!!r.prior_disclosures?.length && (
          <div className="pr-keep mt-1 text-[9px] text-slate-600">
            <b>직전 4주 회사 공시</b> — {r.prior_disclosures.map((x) => `${md(x.date)} ${x.title}`).join(' · ')}
          </div>
        )}
        <H no="4.">회사 고유 이슈</H>
        <Body rep={rep} keyName="idiosyncratic" />
        <Fig title={`그림 2. 일별 등락률 — ${target} · 비교기업 평균 · 시장`} source="KRX" note={hasUnusual ? `빨간 막대: 시장·업종 흐름과 다른 이례적 변동일` : undefined}>
          <WeeklyCompareChart days={r.days} targetName={target} />
        </Fig>
        <Fig title="표 2. 일별 등락과 주요 공시·보도" source="KRX, OpenDART, 언론 보도"
          note={[r.days.some((d) => d.diverged) ? '음영: 비교기업과 방향이 반대이거나 3%p 이상 차이가 난 날' : '', hasUnusual ? '● 시장·업종 흐름과 다른 이례적 변동일' : ''].filter(Boolean).join(' · ') || undefined}>
          <table className="w-full text-[9.5px]">
            <thead><tr><Th>날짜</Th><Th right>{target}</Th><Th right>비교기업 평균</Th><Th right>시장</Th><Th right>거래량(평소 대비)</Th><Th>주요 공시·보도</Th></tr></thead>
            <tbody>
              {r.days.map((d0, i) => {
                // 공시·보도는 '처음 반영된 거래일'에만 표시 (주말 공시는 다음 거래일)
                const prev = i > 0 ? r.days[i - 1].date : ''
                const fresh = <T extends { date: string }>(xs?: T[]) => xs?.filter((x) => x.date > prev && x.date <= d0.date)
                const d = { ...d0, disclosures: fresh(d0.disclosures), news: fresh(d0.news) }
                return (
                <tr key={d.date} className={`align-top ${d.abnormal ? 'bg-red-50' : d.diverged ? 'bg-amber-50' : ''}`}>
                  <Td className="whitespace-nowrap">{md(d.date)}({d.weekday}){d.abnormal ? ' ●' : ''}</Td>
                  <Td right strong>{pct(d.ret, 1)}</Td>
                  <Td right>{pct(d.peer_avg, 1)}</Td>
                  <Td right>{pct(d.market, 1)}</Td>
                  <Td right>{d.volume_ratio != null ? `${d.volume_ratio.toFixed(1)}배` : '—'}</Td>
                  <Td>
                    {d.disclosures?.map((x) => <div key={x.url}>[공시] {x.title}</div>)}
                    {d.news?.map((x) => <div key={x.url}>[보도] {x.event}</div>)}
                    {!d.disclosures?.length && !d.news?.length && (d.abnormal ? '확인된 공시·보도 없음' : '—')}
                  </Td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </Fig>
        <H no="5.">변동성 · 거래량</H>
        <Body rep={rep} keyName="volatility" />
        <Fig title="표 3. 이번 주 등락폭·거래량 (평소 대비)" source="KRX" note="평소: 직전 약 3개월(거래량은 직전 1개월) 평균">
          <table className="w-full text-[9.5px]">
            <thead><tr><Th>항목</Th><Th right>이번 주</Th><Th right>평소</Th><Th right>평소 대비</Th></tr></thead>
            <tbody>
              <tr><Td>하루 등락폭(표준적 수준)</Td><Td right>{vol.ret_std_week != null ? `${(vol.ret_std_week * 100).toFixed(1)}%` : '—'}</Td><Td right>{vol.ret_std_60d != null ? `${(vol.ret_std_60d * 100).toFixed(1)}%` : '—'}</Td><Td right strong>{vol.std_ratio != null ? `${vol.std_ratio.toFixed(1)}배` : '—'}</Td></tr>
              <tr><Td>장중 고저 폭</Td><Td right>{vol.range_week != null ? `${(vol.range_week * 100).toFixed(1)}%` : '—'}</Td><Td right>{vol.range_60d != null ? `${(vol.range_60d * 100).toFixed(1)}%` : '—'}</Td><Td right strong>{vol.range_ratio != null ? `${vol.range_ratio.toFixed(1)}배` : '—'}</Td></tr>
              <tr><Td>일평균 거래량</Td><Td right>{vol.volume_week != null ? `${Math.round(vol.volume_week).toLocaleString()}주` : '—'}</Td><Td right>{vol.volume_20d != null ? `${Math.round(vol.volume_20d).toLocaleString()}주` : '—'}</Td><Td right strong>{vol.volume_ratio != null ? `${vol.volume_ratio.toFixed(1)}배` : '—'}</Td></tr>
            </tbody>
          </table>
        </Fig>
      </section>

      {/* ============ 3쪽 */}
      <section>
        <H no="6.">비교기업별 움직임</H>
        <Fig title="표 4. 비교기업 주간 수익률" source="KRX">
          <table className="w-full text-[9.5px]">
            <thead><tr><Th>기업</Th><Th right>주간 수익률</Th><Th right>{target} 대비</Th><Th right>{target}와 같은 방향</Th></tr></thead>
            <tbody>
              <tr className="bg-blue-50 font-semibold"><Td>{target}</Td><Td right>{pct(s.target_return)}</Td><Td right>—</Td><Td right>—</Td></tr>
              {[...r.peers].sort((a, b) => (b.week_return ?? -9) - (a.week_return ?? -9)).map((p) => (
                <tr key={p.company_id}>
                  <Td>{p.name}</Td>
                  <Td right>{p.available ? pct(p.week_return) : '시세 없음'}</Td>
                  <Td right>{p.available && p.week_return != null ? pp(p.week_return - s.target_return) : '—'}</Td>
                  <Td right>{p.available ? `${r.week.trading_days}일 중 ${p.same_dir_days}일` : '—'}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Fig>
        <H no="7.">이번 주 공시·보도</H>
        {events.length ? (
          <Fig title="표 5. 고객사·비교기업 공시와 보도" source={r.news_enabled ? 'OpenDART, 언론 보도' : 'OpenDART'} note={events.some((e) => e.estimated) ? '* 보도일 추정' : undefined}>
            <table className="w-full text-[9.5px]">
              <thead><tr><Th>일자</Th><Th>기업</Th><Th>구분</Th><Th>내용</Th></tr></thead>
              <tbody>{events.map((x, i) => (
                <tr key={i} className="align-top"><Td className="whitespace-nowrap">{md(x.date)}{x.estimated ? '*' : ''}</Td>
                  <Td className="whitespace-nowrap">{x.company}</Td><Td>{x.kind}</Td><Td>{x.title}{x.extra && <span className="text-slate-500"> ({x.extra})</span>}</Td></tr>
              ))}</tbody>
            </table>
          </Fig>
        ) : <p className="text-slate-500">이번 주 고객사·비교기업 공시와 보도는 없었다.</p>}

        <div className="pr-keep mt-6 border-t border-slate-300 pt-2 text-[8.5px] leading-relaxed text-slate-500">
          본 자료는 정보 제공 목적이며 투자 권유가 아닙니다. 공시·보도와 주가 변동의 시점이 겹치더라도 인과관계를 뜻하지는 않습니다.
        </div>
      </section>
    </div>
  )
}
