// 고객사 프로젝트 첫 화면 — 공통 STEP 1(비교군) 아래로 두 갈래 분석을 고른다.
//  · 통합 분석: 재무·수주·밸류에이션·주가·뉴스를 모두 보는 기존 STEP 2~4
//  · 주간 주가 분석: 월~일 1주 단위로 비교군과의 주가 동조·개별 요인·변동성만 보는 분석
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { analysisApi, VERDICT_TONE, won } from '../apiAnalysis'
import { weeklyApi } from '../apiWeekly'
import { Badge, Card, fmtDateTime } from '../ui'
import { stepStates, useProject } from './ProjectLayout'

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`)

export default function ProjectHome() {
  const { project } = useProject()
  const ps = project.peer_state
  const st = stepStates(project)
  const weekly = useQuery({ queryKey: ['weekly', project.id], queryFn: () => weeklyApi.list(project.id) })
  const lastWeekly = weekly.data?.find((w) => w.status === 'succeeded')
  const runs = useQuery({ queryKey: ['runs', project.id], queryFn: () => analysisApi.runs(project.id) })
  const lr = runs.data?.find((r) => r.status === 'succeeded' || r.status === 'partial')
  const confirmed = !!ps.latest_version_no

  return (
    <div className="space-y-5">
      {/* 공통: 비교군 */}
      <Card className="flex flex-wrap items-center justify-between gap-4 p-5">
        <div>
          <div className="text-xs font-semibold text-slate-500">공통 · STEP 1</div>
          <div className="mt-0.5 text-base font-semibold">동종업계 비교군</div>
          <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">
            {confirmed ? <>비교군 <b>v{ps.latest_version_no}</b> 확정{ps.draft_dirty && <span className="ml-1 text-amber-600">(작업본 수정 중)</span>} — 두 분석 모두 이 비교군을 사용합니다.</>
              : '비교군을 먼저 확정해야 두 분석을 실행할 수 있습니다.'}
          </div>
        </div>
        <Link to="peers" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">
          {confirmed ? '비교군 보기·수정' : '비교군 탐색·확정하기'} →
        </Link>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* 통합 분석 */}
        <Card className="flex flex-col p-6">
          <div className="flex items-center gap-2">
            <span className="rounded bg-blue-700 px-2 py-0.5 text-[11px] font-semibold text-white">통합 분석</span>
            <span className="text-xs text-slate-500">STEP 2 ~ 4</span>
          </div>
          <h2 className="mt-2 text-lg font-bold">기업 가치평가 · 종합 분석</h2>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
            재무(최대 5년 정상화)·수주잔고·비교기업 배수·자사 과거 밸류에이션으로 적정주가 범위를 산출하고,
            주가 변동 원인·공시·뉴스까지 근거 기반 보고서(분석리포트·상세분석 PDF)로 정리합니다.
          </p>
          <div className="mt-4 rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-950">
            {ps.latest_result_run_id && lr ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-slate-500">최근 결과 {fmtDateTime(lr.finished_at ?? lr.started_at)}</span>
                {lr.summary?.verdict && <Badge tone={VERDICT_TONE[lr.summary.verdict] ?? 'slate'}>{lr.summary.verdict}</Badge>}
                {lr.summary?.fair_value?.base != null && <span className="tabular">적정주가(기준) {won(lr.summary.fair_value.base)}</span>}
                {ps.needs_reanalysis && <span className="text-xs text-amber-700">· 비교군 변경 — 재분석 권장</span>}
              </div>
            ) : <span className="text-slate-500">{st[2].label}</span>}
          </div>
          <div className="mt-auto flex gap-2 pt-5">
            <Link to="analysis" className={`rounded-md px-3 py-1.5 text-sm font-medium ${confirmed ? 'bg-blue-700 text-white hover:bg-blue-600' : 'pointer-events-none bg-slate-200 text-slate-400'}`}>
              STEP 2 데이터 수집·분석
            </Link>
            {ps.latest_result_run_id && <Link to="results" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">STEP 3 결과 보기</Link>}
            {ps.latest_result_run_id && <Link to="history" className="rounded-md px-3 py-1.5 text-sm text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800">이력</Link>}
          </div>
        </Card>

        {/* 주간 주가 분석 */}
        <Card className="flex flex-col p-6">
          <div className="flex items-center gap-2">
            <span className="rounded bg-violet-700 px-2 py-0.5 text-[11px] font-semibold text-white">주간 주가 분석</span>
            <span className="text-xs text-slate-500">월~일 1주 단위</span>
          </div>
          <h2 className="mt-2 text-lg font-bold">주가 동조 · 개별 요인 · 변동성</h2>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
            밸류에이션 없이 주가만 봅니다. 선택한 한 주 동안 {project.company.name} 주가가 비교군·시장과 같은 방향으로 움직였는지,
            {project.company.name}만의 공시·보도로 인한 개별 변동이 있었는지, 변동성·거래량이 평소보다 컸는지를 일별로 분석합니다.
          </p>
          <div className="mt-4 rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-950">
            {lastWeekly?.summary ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-slate-500">최근 {lastWeekly.week_start} 주</span>
                <span className={`tabular font-semibold ${lastWeekly.summary.target_return >= 0 ? 'text-red-600' : 'text-blue-600'}`}>{pct(lastWeekly.summary.target_return)}</span>
                <span className="text-slate-600 dark:text-slate-300">{lastWeekly.summary.pattern_label}</span>
                <span className="text-xs text-slate-500">· 분석한 주 {weekly.data?.filter((w) => w.status === 'succeeded').length}개</span>
              </div>
            ) : <span className="text-slate-500">아직 분석한 주가 없습니다.</span>}
          </div>
          <div className="mt-auto flex gap-2 pt-5">
            <Link to="weekly" className={`rounded-md px-3 py-1.5 text-sm font-medium ${confirmed ? 'bg-violet-700 text-white hover:bg-violet-600' : 'pointer-events-none bg-slate-200 text-slate-400'}`}>
              주 선택해 분석하기
            </Link>
          </div>
        </Card>
      </div>
    </div>
  )
}
