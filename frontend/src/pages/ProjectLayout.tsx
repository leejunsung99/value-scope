import { useQuery } from '@tanstack/react-query'
import { Link, Navigate, NavLink, Outlet, useOutletContext, useParams } from 'react-router-dom'
import { api, type Project } from '../api'
import { Badge, MARKET_LABELS, Notice, Spinner, STEPS, fmtDateTime } from '../ui'

export function useProject() {
  return useOutletContext<{ project: Project }>()
}

type StepState = { label: string; tone: 'green' | 'blue' | 'amber' | 'slate' }

/** 각 STEP 탭에 표시할 진행 상태 */
export function stepStates(p: Project): Record<number, StepState> {
  const ps = p.peer_state
  const running = ps.last_run && (ps.last_run.status === 'running' || ps.last_run.status === 'queued')
  return {
    1: ps.last_search?.status === 'running' ? { label: '검색 중', tone: 'blue' }
      : ps.latest_version_no ? (ps.draft_dirty ? { label: `v${ps.latest_version_no} · 수정 중`, tone: 'amber' } : { label: `v${ps.latest_version_no} 확정`, tone: 'green' })
        : { label: '확정 필요', tone: 'slate' },
    2: running ? { label: '분석 중', tone: 'blue' }
      : !ps.latest_version_no ? { label: '비교군 확정 후', tone: 'slate' }
        : ps.needs_reanalysis ? { label: ps.latest_result_run_id ? '재분석 필요' : '실행 필요', tone: 'amber' }
          : { label: '완료', tone: 'green' },
    3: ps.latest_result_run_id ? { label: '결과 있음', tone: 'green' } : { label: '결과 없음', tone: 'slate' },
    4: ps.latest_result_run_id ? { label: '이력 있음', tone: 'green' } : { label: '—', tone: 'slate' },
  }
}

/** 프로젝트를 열면 마지막 작업 단계로 이동 (결과가 있으면 STEP 3) */
export function ProjectIndexRedirect() {
  const { project } = useProject()
  const ps = project.peer_state
  const running = ps.last_run && (ps.last_run.status === 'running' || ps.last_run.status === 'queued')
  const target = running ? 'analysis' : ps.latest_result_run_id ? 'results' : ps.latest_version_no ? 'analysis' : 'peers'
  return <Navigate to={target} replace />
}

function tabCls(active: boolean, tone: 'blue' | 'violet') {
  const on = tone === 'blue' ? 'border-blue-600 font-semibold text-blue-700 dark:text-blue-400' : 'border-violet-600 font-semibold text-violet-700 dark:text-violet-300'
  return `border-b-2 px-3 pb-2 pt-1 text-sm ${active ? on : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'}`
}

export default function ProjectLayout() {
  const { projectId = '' } = useParams()
  const q = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.project(projectId),
    // 분석 실행 중에는 상태를 주기적으로 갱신해 탭·결과 링크가 자동으로 바뀌도록 함
    refetchInterval: (query) => {
      const r = query.state.data?.peer_state.last_run
      return r && (r.status === 'running' || r.status === 'queued') ? 3000 : false
    },
  })

  if (q.isLoading) return <div className="flex items-center gap-2 p-8 text-sm text-slate-500"><Spinner /> 프로젝트 불러오는 중…</div>
  if (q.isError || !q.data) return <div className="p-8"><Notice tone="red">{(q.error as Error)?.message ?? '프로젝트를 찾을 수 없습니다.'}</Notice></div>
  const p = q.data
  const c = p.company
  const search = p.peer_state.last_search
  const lastRun = p.peer_state.last_run
  const states = stepStates(p)
  const toneCls: Record<StepState['tone'], string> = {
    green: 'text-emerald-600 dark:text-emerald-400', blue: 'text-blue-600 dark:text-blue-400',
    amber: 'text-amber-600 dark:text-amber-400', slate: 'text-slate-400',
  }

  return (
    <div>
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur dark:border-slate-800 dark:bg-slate-900/95">
        <div className="flex items-center justify-between gap-6 px-8 pt-4">
          <div className="min-w-0">
            <div className="text-xs text-slate-500"><Link to="/" className="hover:underline">고객사 프로젝트</Link> /</div>
            <div className="mt-0.5 flex items-center gap-2">
              <h1 className="truncate text-xl font-bold tracking-tight">{c.name}</h1>
              <span className="tabular text-sm text-slate-500">{c.stock_code ?? '—'}</span>
              <Badge>{MARKET_LABELS[c.market] ?? c.market}</Badge>
              {c.is_verified ? <Badge tone="green">DART 검증</Badge> : <Badge tone="amber" title={c.verification_note ?? ''}>미검증</Badge>}
            </div>
          </div>
          <dl className="flex shrink-0 gap-6 text-xs">
            <div>
              <dt className="text-slate-500">분석 기준일</dt>
              <dd className="mt-0.5 font-medium">{p.last_analysis_at ? fmtDateTime(p.last_analysis_at) : '분석 전'}</dd>
            </div>
            <div>
              <dt className="text-slate-500">비교군</dt>
              <dd className="mt-0.5 font-medium">
                {p.peer_state.latest_version_no ? `v${p.peer_state.latest_version_no}` : '미확정'}
                {p.peer_state.draft_dirty && <span className="ml-1 text-amber-600">수정 중</span>}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">상태</dt>
              <dd className="mt-0.5 font-medium">
                {search?.status === 'running' ? <span className="text-blue-600">동종업계 검색 중</span>
                  : lastRun?.status === 'running' || lastRun?.status === 'queued' ? <span className="text-blue-600">분석 중</span>
                    : lastRun?.status === 'failed' ? <span className="text-red-600">최근 분석 실패</span>
                      : p.peer_state.latest_result_run_id ? '분석 완료' : '대기'}
              </dd>
            </div>
          </dl>
        </div>
        <nav className="mt-3 flex items-end gap-1 px-6" aria-label="분석 단계">
          <NavLink end to="" className={({ isActive }) => tabCls(isActive, 'blue')}>개요</NavLink>
          {STEPS.filter((s) => s.no === 1).map((s) => (
            <NavLink key={s.no} to={s.path} className={({ isActive }) => tabCls(isActive, 'blue')}>
              <span className="mr-1.5 text-[11px] font-semibold opacity-70">{s.label}</span>{s.title}
              <span className={`ml-1.5 text-[11px] font-medium ${toneCls[states[s.no].tone]}`}>· {states[s.no].label}</span>
            </NavLink>
          ))}
          {/* 분기 1: 통합 분석 */}
          <div className="ml-3 flex items-end gap-1 border-l border-slate-200 pl-3 dark:border-slate-700">
            <span className="mb-2 rounded bg-blue-700 px-1.5 py-0.5 text-[10px] font-semibold text-white">통합 분석</span>
            {STEPS.filter((s) => s.no > 1).map((s) => (
              <NavLink key={s.no} to={s.path} className={({ isActive }) => tabCls(isActive, 'blue')}>
                <span className="mr-1.5 text-[11px] font-semibold opacity-70">{s.label}</span>{s.title}
                <span className={`ml-1.5 text-[11px] font-medium ${toneCls[states[s.no].tone]}`}>· {states[s.no].label}</span>
              </NavLink>
            ))}
          </div>
          {/* 분기 2: 주간 주가 분석 */}
          <div className="ml-3 flex items-end gap-1 border-l border-slate-200 pl-3 dark:border-slate-700">
            <NavLink to="weekly" className={({ isActive }) => tabCls(isActive, 'violet')}>
              <span className="mr-1.5 rounded bg-violet-700 px-1.5 py-0.5 text-[10px] font-semibold text-white">주간</span>주간 주가 분석
            </NavLink>
          </div>
        </nav>
      </header>
      <div className="px-8 py-6">
        <Outlet context={{ project: p }} />
      </div>
    </div>
  )
}
