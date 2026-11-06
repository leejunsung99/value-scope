import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'

function StatusDot({ ok, label, hint }: { ok: boolean; label: string; hint: string }) {
  return (
    <div className="flex items-center justify-between text-xs" title={hint}>
      <span className="text-slate-400">{label}</span>
      <span className={`flex items-center gap-1.5 ${ok ? 'text-emerald-400' : 'text-slate-500'}`}>
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-slate-600'}`} />
        {ok ? '연결' : '미설정'}
      </span>
    </div>
  )
}

export default function Layout() {
  const [dark, setDark] = useState(document.documentElement.classList.contains('dark'))
  const status = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 60_000 })
  const s = status.data

  const toggle = () => {
    const next = !dark
    document.documentElement.classList.toggle('dark', next)
    localStorage.setItem('vs-theme', next ? 'dark' : 'light')
    setDark(next)
  }

  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col bg-slate-900 text-slate-200 dark:border-r dark:border-slate-800">
        <div className="px-5 py-5">
          <div className="text-lg font-bold tracking-tight text-white">
            Value<span className="text-blue-400">Scope</span>
          </div>
          <div className="mt-0.5 text-[11px] text-slate-400">AI 기업가치 분석 플랫폼</div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `block rounded-md px-3 py-2 text-sm ${isActive ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-800/60'}`
            }
          >
            고객사 프로젝트
          </NavLink>
        </nav>
        <div className="space-y-1.5 border-t border-slate-800 px-5 py-4">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">데이터 연동</div>
          {status.isError ? (
            <div className="text-xs text-red-400">백엔드 연결 실패</div>
          ) : (
            <>
              <StatusDot ok={!!s?.database_ok} label="PostgreSQL" hint="DATABASE_URL" />
              <StatusDot ok={!!s?.integrations.dart} label="OpenDART" hint="DART_API_KEY" />
              <StatusDot ok={!!s?.integrations.krx} label="KRX" hint="KRX_API_KEY" />
              <StatusDot ok={!!s?.integrations.ai_ready} label={`AI (${s?.integrations.ai_provider ?? '-'})`} hint="AI_PROVIDER / AI_API_KEY" />
            </>
          )}
        </div>
        <div className="border-t border-slate-800 px-3 py-3">
          <button onClick={toggle} className="w-full rounded-md px-3 py-1.5 text-left text-xs text-slate-400 hover:bg-slate-800">
            {dark ? '☀ 라이트 모드' : '☾ 다크 모드'}
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
