// 주간 주가 분석 (통합 분석과 별도 분기) — 월~일 1주를 골라 고객사 주가가 비교군과 같이 움직였는지,
// 개별 요인(공시·보도)이 있었는지, 변동성이 컸는지만 본다. 밸류에이션은 하지 않는다.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { addDays, isoWeekOf, mondayOf, mondayOfIsoWeek, toISO, weeklyApi, type WeeklyResult, type WeeklyRun } from '../apiWeekly'
import { WeeklyDailyChart, WeeklyPathChart } from '../charts'
import { Badge, Button, Card, Empty, Notice, Spinner, fmtDateTime } from '../ui'
import { useProject } from './ProjectLayout'

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}%`)
const PATTERN_TONE: Record<string, 'green' | 'red' | 'amber' | 'blue' | 'slate'> = { comove: 'blue', idiosyncratic: 'red', mixed: 'amber' }

function weekLabel(mon: string) {
  const sun = addDays(mon, 6)
  return `${mon.replace(/-/g, '.')}(월) ~ ${sun.slice(5).replace('-', '.')}(일)`
}

function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">{title}</h2>{right}</div>
      {children}
    </Card>
  )
}

function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`tabular mt-0.5 text-lg font-bold ${tone ?? ''}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  )
}

// ------------------------------------------------------------------ 주 선택
function WeekPicker({ value, onChange, earliest, latest }: { value: string; onChange: (mon: string) => void; earliest?: string; latest?: string }) {
  const canPrev = !earliest || addDays(value, -7) >= earliest
  const canNext = !latest || addDays(value, 7) <= latest
  const thisMon = toISO(mondayOf(new Date()))
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button onClick={() => onChange(addDays(value, -7))} disabled={!canPrev} title="이전 주">◀</Button>
      <input type="week" value={isoWeekOf(value)} min={earliest ? isoWeekOf(earliest) : undefined} max={latest ? isoWeekOf(latest) : undefined}
        onChange={(e) => { const m = mondayOfIsoWeek(e.target.value); if (m) onChange(m) }}
        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900" />
      <Button onClick={() => onChange(addDays(value, 7))} disabled={!canNext} title="다음 주">▶</Button>
      <span className="tabular text-sm font-semibold">{weekLabel(value)}</span>
      <span className="flex gap-1 text-xs">
        <button className="rounded px-2 py-0.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={() => onChange(addDays(thisMon, -7))}>지난주</button>
        <button className="rounded px-2 py-0.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={() => onChange(thisMon)}>이번 주</button>
      </span>
    </div>
  )
}

// ------------------------------------------------------------------ 리포트 출력
function WeeklyPdfButtons({ run }: { run: WeeklyRun }) {
  const pdf = useMutation({ mutationFn: () => weeklyApi.downloadPdf(run.project_id, run.id) })
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
      <button onClick={() => pdf.mutate()} disabled={pdf.isPending}
        className="inline-flex items-center gap-1.5 rounded-md bg-violet-700 px-3 py-1.5 font-medium text-white hover:bg-violet-600 disabled:opacity-60">
        {pdf.isPending ? <><Spinner /> 만드는 중…</> : '주간 리포트 PDF'}
      </button>
      <a href={`/print/projects/${run.project_id}/weekly/${run.id}?autoprint=1`} target="_blank" rel="noreferrer" className="text-slate-500 hover:text-violet-700 hover:underline">브라우저 인쇄로 저장</a>
      {pdf.isError && <span className="text-red-600">{(pdf.error as Error).message}</span>}
    </div>
  )
}

// ------------------------------------------------------------------ 결과
function WeeklyView({ run }: { run: WeeklyRun }) {
  const { project } = useProject()
  const r = run.result as WeeklyResult
  const rep = run.report
  const s = r.summary
  const names = Object.fromEntries(r.peers.map((p) => [String(p.company_id), p.name]))
  const peerById = Object.fromEntries(r.peers.map((p) => [p.company_id, p]))
  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="text-xs text-slate-500">
          주간 주가 분석 · {weekLabel(r.week.start)} · 거래일 {r.week.trading_days}일 · 기준 {r.week.base_date} 종가 · 비교군 v{run.peer_version_no} · 분석 {fmtDateTime(run.finished_at)}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <Badge tone={PATTERN_TONE[s.pattern]}>{s.pattern_label}</Badge>
          <Badge tone={s.volatility_flags.length ? 'red' : 'slate'}>{s.volatility_label}</Badge>
          {s.abnormal_days.length > 0 && <Badge tone="red">이상 변동일 {s.abnormal_days.map((d) => d.slice(5)).join(', ')}</Badge>}
        </div>
        {rep?.headline && <div className="mt-2 text-base font-semibold">{rep.headline}</div>}
        <WeeklyPdfButtons run={run} />
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Kpi label={`${project.company.name} 주간`} value={pct(s.target_return)} tone={s.target_return >= 0 ? 'text-red-600' : 'text-blue-600'}
            sub={r.week.last_close ? `종가 ${r.week.last_close.toLocaleString()}원` : undefined} />
          <Kpi label="비교군 가중평균" value={pct(s.peer_return)} sub={`상장 비교기업 ${r.peers.filter((p) => p.available).length}개`} />
          <Kpi label="시장 대용지수" value={pct(s.market_return)} sub={r.model.market_label.split(' ')[0]} />
          <Kpi label="비교군 대비 초과" value={pct(s.excess_vs_peers)} sub="고객사 − 비교군" />
          <Kpi label="개별 요인 합계" value={pct(s.residual_sum)} sub={s.z_week != null ? `z = ${s.z_week.toFixed(2)} (|z|≥${r.thresholds.z_week} 개별 우세)` : undefined} />
          <Kpi label="같은 방향" value={`${s.same_dir_days}/${s.judged_days}일`} sub="고객사 vs 비교군 일별 부호" />
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="주가 경로 — 고객사 · 비교군 · 시장 (직전 거래일 = 100)">
          <WeeklyPathChart chart={r.chart} names={names} targetName={project.company.name} />
          <div className="text-[11px] text-slate-500">회색 가는 선 = 개별 비교기업. 비교군 평균은 STEP 1 적합성 점수 × 활용 구분 가중.</div>
        </Section>
        <Section title="일별 — 실제 vs 기대(시장+비교군) vs 개별 요인">
          <WeeklyDailyChart days={r.days} />
          <div className="text-[11px] text-slate-500">
            기대수익률 = α + β시장 {r.model.beta_market.toFixed(2)} × 시장 + β산업 {(r.model.beta_industry ?? 0).toFixed(2)} × 비교군(시장 제거분) — 해당 주 이전 {r.model.n}거래일({r.model.period.join('~')})로 추정, R² {r.model.r2.toFixed(2)}.
            빨간 막대 = 이상 변동(|z| ≥ {r.thresholds.z_day}).
          </div>
        </Section>
      </div>

      {rep && (
        <Section title="주간 해석" right={<span className="text-[11px] text-slate-500">{rep.status === 'generated' ? `AI ${rep.model ?? ''}` : '규칙 기반'} · {rep.verification}</span>}>
          <div className="space-y-3">
            {rep.sections.filter((sec) => sec.paragraphs.length).map((sec) => (
              <div key={sec.key}>
                <div className="mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">{sec.title}</div>
                {sec.paragraphs.map((p) => (
                  <p key={p.ref} className="mb-1 text-sm leading-relaxed">{p.text} <span className="text-[10px] text-slate-400">[{p.evidence_ids.join(', ')}]</span></p>
                ))}
              </div>
            ))}
          </div>
          {rep.held.length > 0 && (
            <details className="mt-2 text-xs text-slate-500">
              <summary className="cursor-pointer">검증 미통과로 게시하지 않은 문단 {rep.held.length}개</summary>
              {rep.held.map((h) => <div key={h.ref} className="mt-1 border-l-2 border-amber-300 pl-2"><span className="line-through">{h.text}</span> — {h.issues.join(' / ')}</div>)}
            </details>
          )}
        </Section>
      )}

      <Section title="일별 상세 — 개별 변동과 공시·보도">
        <div className="overflow-x-auto">
          <table className="tabular w-full text-xs">
            <thead className="text-left text-[11px] text-slate-500">
              <tr><th className="py-1">날짜</th><th className="text-right">고객사</th><th className="text-right">비교군</th><th className="text-right">시장</th>
                <th className="text-right">기대</th><th className="text-right">개별 요인</th><th className="text-right">z</th><th className="text-right">거래량</th>
                <th className="pl-3">공시·보도 (당일~2일 전)</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {r.days.map((d) => (
                <tr key={d.date} className={`align-top ${d.abnormal ? 'bg-red-50/70 dark:bg-red-950/30' : ''}`}>
                  <td className="whitespace-nowrap py-1.5">{d.date.slice(5)}({d.weekday}){d.abnormal && <Badge tone="red">이상</Badge>}</td>
                  <td className={`text-right font-semibold ${d.ret >= 0 ? 'text-red-600' : 'text-blue-600'}`}>{pct(d.ret, 2)}</td>
                  <td className="text-right">{pct(d.peer_avg, 2)}{d.same_dir === false && <span className="ml-1 text-amber-600" title="고객사와 반대 방향">↔</span>}</td>
                  <td className="text-right">{pct(d.market, 2)}</td>
                  <td className="text-right text-slate-500">{pct(d.expected, 2)}</td>
                  <td className="text-right font-medium">{pct(d.residual, 2)}</td>
                  <td className="text-right">{d.z != null ? d.z.toFixed(1) : '—'}</td>
                  <td className="text-right">{d.volume_ratio != null ? `${d.volume_ratio.toFixed(1)}배` : '—'}</td>
                  <td className="pl-3">
                    {d.disclosures?.map((x) => <div key={x.url}><Badge tone="blue">공시</Badge> <a href={x.url} target="_blank" rel="noreferrer" className="hover:underline">{x.date.slice(5)} {x.title}</a></div>)}
                    {d.news?.map((x) => <div key={x.url}><Badge tone="violet">보도</Badge> <a href={x.url} target="_blank" rel="noreferrer" className="hover:underline">{x.date.slice(5)} {x.event}</a> <span className="text-slate-500">{x.outlets.join(', ')}</span></div>)}
                    {d.abnormal && <div className="text-[11px] text-slate-500">{d.explanation}</div>}
                    {!d.disclosures?.length && !d.news?.length && !d.abnormal && <span className="text-slate-400">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-1 text-[11px] text-slate-500">거래량 = 직전 20거래일 평균 대비 배수. ↔ = 고객사와 비교군이 반대 방향. 시간적 근접은 인과관계를 뜻하지 않습니다.</div>
      </Section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="비교기업별 주간 움직임">
          <table className="tabular w-full text-xs">
            <thead className="text-left text-[11px] text-slate-500">
              <tr><th className="py-1">기업</th><th className="text-right">가중치</th><th className="text-right">주간</th><th className="text-right">같은 방향</th><th className="text-right">60일 상관</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              <tr className="bg-blue-50/60 font-semibold dark:bg-blue-950/30"><td className="py-1">{project.company.name} (고객사)</td><td className="text-right">—</td><td className="text-right">{pct(s.target_return)}</td><td className="text-right">—</td><td className="text-right">—</td></tr>
              {[...r.peers].sort((a, b) => (b.week_return ?? -9) - (a.week_return ?? -9)).map((p) => (
                <tr key={p.company_id}>
                  <td className="py-1">{p.name}</td>
                  <td className="text-right text-slate-500">{p.weight.toFixed(2)}</td>
                  <td className={`text-right ${(p.week_return ?? 0) >= 0 ? 'text-red-600' : 'text-blue-600'}`}>{p.available ? pct(p.week_return) : '시세 없음'}</td>
                  <td className="text-right">{p.available ? `${p.same_dir_days}/${r.week.trading_days}일` : '—'}</td>
                  <td className="text-right">{p.corr_60d != null ? p.corr_60d.toFixed(2) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section title="변동성 · 거래량 (직전 대비)">
          <table className="tabular w-full text-xs">
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {[
                ['일별 수익률 변동성', r.volatility.ret_std_week, r.volatility.ret_std_60d, r.volatility.std_ratio, r.thresholds.vol_ratio, '직전 60거래일'],
                ['평균 일중 변동폭 (고가−저가)', r.volatility.range_week, r.volatility.range_60d, r.volatility.range_ratio, r.thresholds.range_ratio, '직전 60거래일'],
              ].map(([label, wv, hv, ratio, th, base]) => (
                <tr key={label as string}><td className="py-1.5">{label}</td><td className="text-right">{wv != null ? `${((wv as number) * 100).toFixed(2)}%` : '—'}</td>
                  <td className="text-right text-slate-500">{base} {hv != null ? `${((hv as number) * 100).toFixed(2)}%` : '—'}</td>
                  <td className={`text-right font-semibold ${ratio != null && (ratio as number) >= (th as number) ? 'text-red-600' : ''}`}>{ratio != null ? `${(ratio as number).toFixed(1)}배` : '—'}</td></tr>
              ))}
              <tr><td className="py-1.5">평균 거래량</td><td className="text-right">{r.volatility.volume_week != null ? Math.round(r.volatility.volume_week).toLocaleString() : '—'}</td>
                <td className="text-right text-slate-500">직전 20거래일 {r.volatility.volume_20d != null ? Math.round(r.volatility.volume_20d).toLocaleString() : '—'}</td>
                <td className={`text-right font-semibold ${(r.volatility.volume_ratio ?? 0) >= r.thresholds.volume_ratio ? 'text-red-600' : ''}`}>{r.volatility.volume_ratio != null ? `${r.volatility.volume_ratio.toFixed(1)}배` : '—'}</td></tr>
            </tbody>
          </table>
          <div className="mt-1 text-[11px] text-slate-500">변동성 {r.thresholds.vol_ratio}배·변동폭 {r.thresholds.range_ratio}배·거래량 {r.thresholds.volume_ratio}배 이상이면 '변동성 확대'.</div>
        </Section>
      </div>

      <Section title={`이번 주 공시·보도 (고객사·비교기업)${r.news_enabled ? '' : ' — 뉴스 검색 제외'}`}>
        {!r.disclosures.length && !r.news.length ? <div className="text-xs text-slate-500">해당 주 공시·보도가 없습니다.</div> : (
          <table className="w-full text-xs">
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {[...r.disclosures.map((x) => ({ ...x, kind: 'disc' as const })), ...r.news.map((x) => ({ ...x, kind: 'news' as const, title: x.event }))]
                .sort((a, b) => b.date.localeCompare(a.date)).map((x, i) => (
                  <tr key={i} className="align-top">
                    <td className="tabular w-20 py-1 text-slate-500">{x.date.slice(5)}{x.kind === 'news' && !x.date_certain ? '*' : ''}</td>
                    <td className="w-28">{x.company || (x.company_id ? peerById[x.company_id]?.name : '')}</td>
                    <td className="w-12">{x.kind === 'disc' ? <Badge tone="blue">공시</Badge> : <Badge tone="violet">보도</Badge>}</td>
                    <td><a href={x.url} target="_blank" rel="noreferrer" className="hover:underline">{x.title}</a>
                      {x.kind === 'news' && <span className="ml-1 text-slate-500">{x.outlets.join(', ')}{x.verification === 'confirmed' ? ' · 공시로 확인' : ' · 기사로만 확인'}</span>}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  )
}

// ------------------------------------------------------------------ page
export default function WeeklyPage() {
  const { project } = useProject()
  const pid = project.id
  const qc = useQueryClient()
  const [sp, setSp] = useSearchParams()
  const range = useQuery({ queryKey: ['weekly-range', pid], queryFn: () => weeklyApi.range(pid) })
  const list = useQuery({
    queryKey: ['weekly', pid], queryFn: () => weeklyApi.list(pid),
    refetchInterval: (q) => (q.state.data?.some((w) => w.status === 'running' || w.status === 'queued') ? 2000 : false),
  })
  const defaultWeek = useMemo(() => addDays(toISO(mondayOf(new Date())), -7), [])
  const [week, setWeek] = useState(sp.get('week') ?? defaultWeek)
  const [includeNews, setIncludeNews] = useState(true)
  const runs = (list.data ?? []).filter((w) => w.week_start === week)
  const selectedId = sp.get('id') ?? runs.find((w) => w.status === 'succeeded')?.id ?? runs[0]?.id
  const detail = useQuery({
    queryKey: ['weekly-run', pid, selectedId], queryFn: () => weeklyApi.get(pid, selectedId!), enabled: !!selectedId,
    refetchInterval: (q) => (q.state.data && (q.state.data.status === 'running' || q.state.data.status === 'queued') ? 2000 : false),
  })
  const active = list.data?.find((w) => w.status === 'running' || w.status === 'queued')
  const start = useMutation({
    mutationFn: () => weeklyApi.start(pid, week, includeNews),
    onSuccess: (w) => { qc.invalidateQueries({ queryKey: ['weekly', pid] }); setSp({ week, id: w.id }) },
  })

  if (!project.peer_state.latest_version_no) {
    return <Empty title="확정된 비교군이 없습니다">주간 분석은 비교군과 함께 움직였는지를 봅니다. 먼저 <Link to="../peers" className="text-blue-600 underline">STEP 1</Link>에서 비교기업을 확정하세요.</Empty>
  }
  const changeWeek = (m: string) => { setWeek(m); setSp({ week: m }) }
  const run = detail.data
  // 같은 주를 여러 번 분석했으면 가장 최근 실행만 목록에 표시
  const history = (list.data ?? []).filter((w, i, arr) => arr.findIndex((x) => x.week_start === w.week_start) === i)
  const weekNotOver = addDays(week, 6) >= toISO(new Date())

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_240px]">
      <div className="space-y-4">
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="space-y-1">
              <div className="text-sm font-semibold">주간 주가 분석 — 주(월~일) 선택</div>
              <WeekPicker value={week} onChange={changeWeek} earliest={range.data?.earliest} latest={range.data?.latest} />
              <div className="text-[11px] text-slate-500">
                밸류에이션 없이 주가만 봅니다: 비교군과 같이 움직였는지 · 고객사만의 공시·보도가 있었는지 · 변동성이 컸는지.
                선택 가능 범위 {range.data ? `${range.data.earliest} ~ ${range.data.latest}` : '…'} (일별 시세 보관 기간 기준)
                {weekNotOver && <span className="text-amber-700"> · 아직 끝나지 않은 주 — 현재까지 거래일만 분석</span>}
              </div>
            </div>
            <div className="flex flex-col items-end gap-1.5">
              <Button variant="primary" disabled={!!active || start.isPending} onClick={() => start.mutate()}>
                {active ? <><Spinner /> {active.week_start} 분석 중… {active.stage ?? ''}</> : runs.length ? '이 주 다시 분석' : '이 주 분석 실행'}
              </Button>
              <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
                <input type="checkbox" checked={includeNews} onChange={(e) => setIncludeNews(e.target.checked)} /> 해당 주 뉴스 검색 포함
              </label>
            </div>
          </div>
          {start.isError && <div className="mt-2"><Notice tone="red">{(start.error as Error).message}</Notice></div>}
        </Card>

        {!selectedId && !active && <Empty title={`${weekLabel(week)} 분석 결과가 없습니다`}>위에서 '이 주 분석 실행'을 누르세요. 한 주 분석은 보통 1~3분 걸립니다(뉴스·AI 해석 포함).</Empty>}
        {run && (run.status === 'running' || run.status === 'queued') && (
          <Card className="flex items-center gap-2 p-5 text-sm"><Spinner /> {weekLabel(run.week_start)} 분석 중 — {run.stage ?? '대기'}</Card>
        )}
        {run?.status === 'failed' && <Notice tone="red">{run.messages?.filter((m) => m.level === 'error').map((m) => m.message).join(' / ') || '분석 실패'}</Notice>}
        {run?.status === 'succeeded' && run.result && <WeeklyView run={run} />}
        {run?.messages && run.status === 'succeeded' && (
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">실행 로그</summary>
            {run.messages.map((m, i) => <div key={i} className={m.level === 'warning' ? 'text-amber-700' : m.level === 'error' ? 'text-red-600' : ''}>· {m.message}</div>)}
          </details>
        )}
      </div>

      <aside>
        <Card className="p-4">
          <div className="mb-2 text-sm font-semibold">분석한 주</div>
          {!history.length ? <div className="text-xs text-slate-500">아직 없습니다.</div> : (
            <ul className="space-y-1 text-xs">
              {history.map((w) => (
                <li key={w.id}>
                  <button onClick={() => { setWeek(w.week_start); setSp({ week: w.week_start, id: w.id }) }}
                    className={`w-full rounded px-2 py-1.5 text-left hover:bg-slate-100 dark:hover:bg-slate-800 ${w.id === selectedId ? 'bg-blue-50 dark:bg-blue-950/40' : ''}`}>
                    <div className="tabular font-medium">{w.week_start.slice(2).replace(/-/g, '.')} 주 <span className="text-slate-400">(월~일)</span></div>
                    <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
                      {w.status === 'succeeded' && w.summary ? <><span className={w.summary.target_return >= 0 ? 'text-red-600' : 'text-blue-600'}>{pct(w.summary.target_return)}</span> · {w.summary.pattern_label.split(' (')[0]}</>
                        : w.status === 'failed' ? <span className="text-red-600">실패</span> : <span className="text-blue-600">진행 중</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </aside>
    </div>
  )
}
