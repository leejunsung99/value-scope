import { useQuery } from '@tanstack/react-query'
import { Link, NavLink, Outlet, useOutletContext, useParams } from 'react-router-dom'
import { api, type Project } from '../api'
import { Badge, MARKET_LABELS, Notice, Spinner, STEPS, fmtDateTime } from '../ui'

export function useProject() {
  return useOutletContext<{ project: Project }>()
}

export default function ProjectLayout() {
  const { projectId = '' } = useParams()
  const q = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId) })

  if (q.isLoading) return <div className="flex items-center gap-2 p-8 text-sm text-slate-500"><Spinner /> 프로젝트 불러오는 중…</div>
  if (q.isError || !q.data) return <div className="p-8"><Notice tone="red">{(q.error as Error)?.message ?? '프로젝트를 찾을 수 없습니다.'}</Notice></div>
  const p = q.data
  const c = p.company
  const search = p.peer_state.last_search

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
                {search?.status === 'running' ? <span className="text-blue-600">동종업계 검색 중</span> : p.last_analysis_status ?? '대기'}
              </dd>
            </div>
          </dl>
        </div>
        <nav className="mt-3 flex gap-1 px-6">
          {STEPS.map((s) => (
            <NavLink
              key={s.no}
              to={s.path}
              className={({ isActive }) =>
                `border-b-2 px-3 pb-2.5 pt-1 text-sm ${isActive ? 'border-blue-600 font-semibold text-blue-700 dark:text-blue-400' : 'border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'}`
              }
            >
              <span className="mr-1.5 text-[11px] font-semibold opacity-70">{s.label}</span>
              {s.title}
            </NavLink>
          ))}
        </nav>
      </header>
      <div className="px-8 py-6">
        <Outlet context={{ project: p }} />
      </div>
    </div>
  )
}
