import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { analysisApi, krw, pct, VERDICT_TONE, won, type AnalysisRun, type StepStatus } from '../apiAnalysis'
import { Badge, Button, Card, CATEGORY_LABELS, Empty, fmtDateTime, Notice, Spinner, USE_TYPE_LABELS } from '../ui'
import { useProject } from './ProjectLayout'

const STEP_LABEL: Record<string, string> = { financials: '재무', disclosures: '공시', contracts: '수주', prices: '주가', news: '뉴스', valuation: '평가' }
const RUN_STATUS: Record<string, [string, 'blue' | 'green' | 'amber' | 'red' | 'slate']> = {
  queued: ['대기', 'slate'], running: ['분석 중', 'blue'], succeeded: ['완료', 'green'], partial: ['일부 완료', 'amber'], failed: ['실패', 'red'],
}

export function RunBadge({ status }: { status: string }) {
  const [l, t] = RUN_STATUS[status] ?? [status, 'slate']
  return <Badge tone={t}>{l}</Badge>
}

function StepCell({ s }: { s?: StepStatus }) {
  if (!s) return <span className="text-slate-300 dark:text-slate-600">·</span>
  const icon = { running: <Spinner className="h-3 w-3" />, done: '✔', failed: '✕', skipped: '–', partial: '◐' }[s.status]
  const cls = { running: 'text-blue-600', done: 'text-emerald-600', failed: 'text-red-600', skipped: 'text-slate-400', partial: 'text-amber-600' }[s.status]
  return <span className={cls} title={s.detail ?? ''}>{icon}</span>
}

export function Step2Analysis() {
  const { project } = useProject()
  const pid = project.id
  const qc = useQueryClient()
  const nav = useNavigate()
  const no = project.peer_state.latest_version_no
  const v = useQuery({ queryKey: ['version', pid, no], queryFn: () => api.version(pid, no), enabled: no > 0 })
  const runs = useQuery({
    queryKey: ['runs', pid], queryFn: () => analysisApi.runs(pid),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'running' || r.status === 'queued') ? 2000 : false),
  })
  const usage = useQuery({ queryKey: ['usage'], queryFn: analysisApi.usage, refetchInterval: 10000 })
  const active = runs.data?.find((r) => r.status === 'running' || r.status === 'queued')
  const latest = runs.data?.[0]
  const detail = useQuery({
    queryKey: ['run', pid, latest?.id], queryFn: () => analysisApi.run(pid, latest!.id), enabled: !!latest,
    refetchInterval: active ? 2000 : false,
  })
  const [includeNews, setIncludeNews] = useState(true)
  const start = useMutation({ mutationFn: () => analysisApi.start(pid, includeNews), onSuccess: () => qc.invalidateQueries({ queryKey: ['runs', pid] }) })
  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && !active) {
      qc.invalidateQueries({ queryKey: ['project', pid] })
      if (latest && (latest.status === 'succeeded' || latest.status === 'partial')) nav(`/projects/${pid}/results?run=${latest.id}`)
    }
    wasActive.current = !!active
  }, [active, latest, pid, qc, nav])

  if (!no) {
    return (
      <Empty title="확정된 비교군이 없습니다">
        분석은 고객사와 확정된 비교기업 전체를 대상으로 실행됩니다. 먼저 <Link to="../peers" className="text-blue-600 underline">STEP 1</Link>에서 비교기업을 확정하세요.
      </Empty>
    )
  }
  const d = detail.data
  const comps = Object.values(d?.progress?.companies ?? {})
  const lastDone = runs.data?.find((r) => r.status === 'succeeded' || r.status === 'partial')
  return (
    <div className="space-y-4">
      {lastDone && !active && (
        <Card className="flex flex-wrap items-center justify-between gap-4 border-blue-200 bg-blue-50/60 p-5 dark:border-blue-900 dark:bg-blue-950/30">
          <div>
            <div className="text-xs font-semibold text-blue-700 dark:text-blue-300">다음 단계 · STEP 3 분석 결과</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
              <span>최근 분석 {fmtDateTime(lastDone.finished_at ?? lastDone.started_at)} (비교군 v{lastDone.peer_version_no}, 주가 기준일 {lastDone.price_as_of ?? '—'})</span>
              {lastDone.summary?.verdict && <Badge tone={VERDICT_TONE[lastDone.summary.verdict] ?? 'slate'}>{lastDone.summary.verdict}</Badge>}
              {lastDone.summary?.fair_value?.base != null && <span className="tabular text-slate-600 dark:text-slate-300">적정주가(기준) {won(lastDone.summary.fair_value.base)} · 현재가 {won(lastDone.summary.price)}</span>}
            </div>
            {project.peer_state.needs_reanalysis && <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">비교군이 v{no}로 바뀌어 이 결과는 이전 비교군(v{lastDone.peer_version_no}) 기준입니다. 재분석을 권장합니다.</div>}
          </div>
          <Button variant="primary" onClick={() => nav(`/projects/${pid}/results?run=${lastDone.id}`)}>STEP 3 결과 보기 →</Button>
        </Card>
      )}
      {latest?.status === 'failed' && !active && (
        <Notice tone="red">최근 분석 실행이 실패했습니다: {latest.summary?.verdict_reason ?? '아래 실행 로그를 확인하세요.'} {lastDone && '— 이전 성공 결과는 STEP 3에서 계속 볼 수 있습니다.'}</Notice>
      )}
      {project.peer_state.draft_dirty && <Notice>STEP 1 작업본이 확정본(v{no})과 다릅니다. 변경을 반영하려면 STEP 1에서 다시 확정하세요.</Notice>}
      {project.peer_state.needs_reanalysis && !active && <Notice tone="blue">비교군 v{no}로 아직 분석하지 않았습니다 — 재분석이 필요합니다.</Notice>}
      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">분석 대상 — {v.data?.label ?? `비교군 v${no}`}</div>
            <div className="text-xs text-slate-500">
              고객사 + 비교기업 {v.data?.member_count ?? '-'}개 · 수집은 증분(이미 받은 데이터 재사용), 평가는 매번 전체 최신 데이터로 재계산
            </div>
            {usage.data && (
              <div className="mt-1 text-[11px] text-slate-500">
                오늘 API 사용량 — DART {usage.data.DART.used_today}/{usage.data.DART.limit} · KRX {usage.data.KRX.used_today}/{usage.data.KRX.limit} (공시알람과 키 공유)
              </div>
            )}
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <Button variant="primary" disabled={!!active || start.isPending} onClick={() => start.mutate()}>
              {active ? <><Spinner /> 분석 진행 중…</> : latest ? '최신화 및 재분석 실행' : '기업 분석 실행'}
            </Button>
            <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400" title="고객사(6개월)·비교기업(3개월)·전방산업 기사를 Claude 웹검색으로 찾습니다. 같은 날 재분석은 검색 결과를 재사용합니다.">
              <input type="checkbox" checked={includeNews} disabled={!!active} onChange={(e) => setIncludeNews(e.target.checked)} />
              뉴스 검색 포함 (고객사·비교기업·전방산업)
            </label>
          </div>
        </div>
        {start.isError && <div className="mt-2"><Notice tone="red">{(start.error as Error).message}</Notice></div>}
      </Card>

      {d && (
        <Card className="p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold">
              실행 {fmtDateTime(d.started_at)} <RunBadge status={d.status} />
              {d.status === 'running' && <span className="text-xs font-normal text-slate-500">{d.progress.stage} {d.progress.stage_detail ? `— ${d.progress.stage_detail}` : ''}</span>}
            </div>
            {(d.status === 'succeeded' || d.status === 'partial') && <Link to={`../results?run=${d.id}`} className="text-sm text-blue-600 hover:underline">결과 보기 →</Link>}
          </div>
          <table className="mt-3 w-full text-sm">
            <thead className="text-left text-[11px] text-slate-500">
              <tr><th className="py-1">기업</th><th>구분</th>{Object.values(STEP_LABEL).map((l) => <th key={l} className="w-12 text-center">{l}</th>)}<th>상태 메모</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {comps.map((c) => {
                const failed = Object.entries(c.steps).filter(([, s]) => s.status === 'failed')
                return (
                  <tr key={c.company_id}>
                    <td className="py-1.5 font-medium">{c.name}</td>
                    <td className="text-xs text-slate-500">{c.role === 'target' ? '고객사' : USE_TYPE_LABELS[c.role as keyof typeof USE_TYPE_LABELS] ?? c.role}{c.category && ` · ${c.category}`}</td>
                    {Object.keys(STEP_LABEL).map((k) => <td key={k} className="text-center"><StepCell s={c.steps[k]} /></td>)}
                    <td className="max-w-md truncate text-xs text-slate-500" title={Object.entries(c.steps).map(([k, s]) => `${STEP_LABEL[k]}: ${s.detail ?? s.status}`).join('\n')}>
                      {failed.length ? <span className="text-red-600">최신화 실패: {failed.map(([k, s]) => `${STEP_LABEL[k]} ${s.detail ?? ''}`).join('; ')}</span> : c.steps.valuation?.detail ?? c.steps.financials?.detail ?? ''}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {!!d.messages?.length && (
            <div className="mt-3 max-h-56 space-y-0.5 overflow-y-auto rounded bg-slate-50 p-2 text-xs dark:bg-slate-950">
              {d.messages.map((m, i) => (
                <div key={i} className={m.level === 'error' ? 'text-red-600' : m.level === 'warning' ? 'text-amber-700 dark:text-amber-400' : 'text-slate-600 dark:text-slate-400'}>
                  {m.at?.slice(11)} {m.message}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      <Card className="p-5">
        <div className="text-sm font-semibold">확정 비교군</div>
        <table className="mt-2 w-full text-sm">
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {v.data?.members?.map((m) => (
              <tr key={m.company_id}><td className="py-1.5">{m.snapshot.company.name} <span className="tabular text-xs text-slate-500">{m.snapshot.company.stock_code ?? ''}</span></td>
                <td><Badge>{m.category}. {CATEGORY_LABELS[m.category]}</Badge></td><td className="text-xs">{USE_TYPE_LABELS[m.use_type]}</td></tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

export function Step4History() {
  const { project } = useProject()
  const pid = project.id
  const runs = useQuery({ queryKey: ['runs', pid], queryFn: () => analysisApi.runs(pid) })
  const peers = useQuery({ queryKey: ['peers', pid], queryFn: () => api.peers(pid) })
  const done = (runs.data ?? []).filter((r) => r.status === 'succeeded' || r.status === 'partial')
  const [pair, setPair] = useState<[string, string] | null>(null)
  const cmp = useQuery({ queryKey: ['compare', pair], queryFn: () => analysisApi.compare(pid, pair![0], pair![1]), enabled: !!pair })
  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="text-sm font-semibold">분석 실행 이력 (Snapshot)</div>
        {!runs.data?.length && <div className="mt-2 text-sm text-slate-500">분석 이력이 없습니다.</div>}
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-[11px] text-slate-500"><tr><th className="py-1">실행 시각</th><th>상태</th><th>비교군</th><th>주가 기준일</th><th className="text-right">적정주가(기준)</th><th className="text-right">현재가</th><th>판단</th><th>AI</th><th /></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {runs.data?.map((r: AnalysisRun, i) => (
              <tr key={r.id}>
                <td className="py-1.5">{fmtDateTime(r.started_at)}</td>
                <td><RunBadge status={r.status} /></td>
                <td>v{r.peer_version_no}</td>
                <td className="tabular">{r.price_as_of ?? '—'}</td>
                <td className="tabular text-right">{won(r.summary?.fair_value?.base)}</td>
                <td className="tabular text-right">{won(r.summary?.price)}</td>
                <td>{r.summary?.verdict ? <Badge tone={VERDICT_TONE[r.summary.verdict] ?? 'slate'}>{r.summary.verdict}</Badge> : '—'}</td>
                <td className="text-xs text-slate-500">{r.ai_model ?? r.ai_provider}</td>
                <td className="space-x-2 text-right text-xs">
                  {(r.status === 'succeeded' || r.status === 'partial') && <Link className="text-blue-600 hover:underline" to={`../results?run=${r.id}`}>보기</Link>}
                  {done.length > 1 && done[0].id !== r.id && done.some((x) => x.id === r.id) && i > 0 && (
                    <button className="text-blue-600 hover:underline" onClick={() => setPair([r.id, done[0].id])}>최신과 비교</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {pair && cmp.data && (
        <Card className="p-5">
          <div className="text-sm font-semibold">과거 분석 vs 최신 분석</div>
          <div className="mt-1 text-xs text-slate-500">
            {fmtDateTime(cmp.data.old.started_at)} (비교군 v{cmp.data.peer_version.old}) → {fmtDateTime(cmp.data.new.started_at)} (v{cmp.data.peer_version.new}) ·
            판단 {cmp.data.verdict.old} → {cmp.data.verdict.new}
          </div>
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-[11px] text-slate-500"><tr><th className="py-1">항목</th><th className="text-right">이전</th><th className="text-right">최신</th><th className="text-right">변화</th></tr></thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {cmp.data.rows.map((row) => {
                const isMoney = !/배|PER|EV|신뢰도/.test(row.label)
                const f = (x: number | null) => x == null ? '데이터 없음' : isMoney ? (row.label.includes('주가') ? won(x) : krw(x)) : row.label.includes('신뢰도') ? `${x.toFixed(0)}점` : `${x.toFixed(1)}배`
                return <tr key={row.label}><td className="py-1">{row.label}</td><td className="tabular text-right">{f(row.old)}</td><td className="tabular text-right">{f(row.new)}</td><td className="tabular text-right">{pct(row.change)}</td></tr>
              })}
              {Object.entries(cmp.data.applied_multiples).map(([k, m]) => (
                <tr key={k}><td className="py-1">적용 멀티플({k === 'peer' ? '비교기업' : '자사 과거'} {m.multiple ?? ''} 중앙값)</td><td className="tabular text-right">{m.old?.toFixed(1) ?? '—'}</td><td className="tabular text-right">{m.new?.toFixed(1) ?? '—'}</td><td /></tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 text-[11px] text-slate-500">평가 변경 사유는 각 실행의 보고서 ⑦·⑧ 섹션과 근거 상세보기에서 확인하세요.</div>
        </Card>
      )}

      <Card className="p-5">
        <div className="text-sm font-semibold">비교군 확정 이력</div>
        <ul className="mt-2 divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {[...(peers.data?.versions ?? [])].reverse().map((v) => (
            <li key={v.id} className="flex justify-between py-1.5"><span><Badge tone="blue">v{v.version_no}</Badge> {v.label}{v.note && <span className="ml-2 text-xs text-slate-500">— {v.note}</span>}</span>
              <span className="text-xs text-slate-500">{fmtDateTime(v.confirmed_at)} · {v.member_count}개</span></li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
