import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, type CompanyHit, type Project } from '../api'
import { Badge, Button, Card, Empty, fmtDateTime, MARKET_LABELS, Modal, Notice, Spinner, STEPS } from '../ui'

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

function ProjectCard({ p }: { p: Project }) {
  const step = STEPS.find((s) => s.no === p.current_step)
  const ps = p.peer_state
  return (
    <Link to={`/projects/${p.id}`} className="group">
      <Card className="h-full p-4 transition-shadow group-hover:shadow-md group-hover:ring-1 group-hover:ring-blue-500/40">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-base font-semibold">{p.company.name}</div>
            <div className="tabular mt-0.5 text-xs text-slate-500">
              {p.company.stock_code ?? '종목코드 없음'} · {MARKET_LABELS[p.company.market] ?? p.company.market}
            </div>
          </div>
          {p.company.is_verified ? <Badge tone="green">DART 검증</Badge> : <Badge tone="amber">미검증</Badge>}
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-y-2 text-xs">
          <dt className="text-slate-500">진행 단계</dt>
          <dd className="text-right font-medium">{step ? `${step.label} ${step.title}` : '-'}</dd>
          <dt className="text-slate-500">비교군</dt>
          <dd className="text-right">
            {ps.latest_version_no > 0 ? `v${ps.latest_version_no} 확정` : '미확정'}
            {ps.draft_dirty && <span className="ml-1 text-amber-600">(수정 중)</span>}
          </dd>
          <dt className="text-slate-500">최근 동종업계 검색</dt>
          <dd className="text-right">{ps.last_search ? fmtDateTime(ps.last_search.started_at) : '없음'}</dd>
          <dt className="text-slate-500">마지막 분석</dt>
          <dd className="text-right">{p.last_analysis_at ? `${fmtDateTime(p.last_analysis_at)} (${p.last_analysis_status})` : '분석 이력 없음'}</dd>
        </dl>
      </Card>
    </Link>
  )
}

function RegisterModal({ open, onClose, dartReady }: { open: boolean; onClose: () => void; dartReady: boolean }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [manual, setManual] = useState(!dartReady)
  const [mName, setMName] = useState('')
  const [mCode, setMCode] = useState('')
  const [mMarket, setMMarket] = useState('KOSDAQ')
  const dq = useDebounced(q.trim())
  const hits = useQuery({
    queryKey: ['company-search', dq],
    queryFn: () => api.searchCompanies(dq),
    enabled: open && !manual && dq.length >= 1,
  })
  const create = useMutation({
    mutationFn: api.createProject,
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      onClose()
      nav(`/projects/${p.id}/peers`)
    },
  })
  const err = create.error as ApiError | null
  const existingId = (err?.detail as { existing_project_id?: string } | undefined)?.existing_project_id

  const pick = (h: CompanyHit) => {
    if (h.existing_project_id) {
      nav(`/projects/${h.existing_project_id}`)
      onClose()
      return
    }
    create.mutate({ corp_code: h.corp_code })
  }

  return (
    <Modal open={open} onClose={onClose} title="새 고객사 등록">
      <div className="mb-3 flex gap-1 rounded-md bg-slate-100 p-1 text-sm dark:bg-slate-800">
        <button className={`flex-1 rounded px-2 py-1 ${!manual ? 'bg-white shadow-sm dark:bg-slate-700' : ''}`} onClick={() => setManual(false)}>
          DART 기업 검색
        </button>
        <button className={`flex-1 rounded px-2 py-1 ${manual ? 'bg-white shadow-sm dark:bg-slate-700' : ''}`} onClick={() => setManual(true)}>
          수동 등록 (미검증)
        </button>
      </div>

      {!manual ? (
        <>
          {!dartReady && <Notice tone="red">DART_API_KEY가 설정되지 않아 공식 기업 검색을 사용할 수 없습니다. .env에 키를 입력하거나 수동 등록을 사용하세요.</Notice>}
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="기업명 또는 종목코드 (예: 선익시스템, 171090)"
            className="mt-2 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-950"
          />
          <div className="mt-3 max-h-80 overflow-y-auto">
            {hits.isFetching && <div className="flex items-center gap-2 p-2 text-sm text-slate-500"><Spinner /> OpenDART 기업목록 검색 중… (최초 1회는 목록 다운로드로 시간이 걸립니다)</div>}
            {hits.isError && <Notice tone="red">{(hits.error as Error).message}</Notice>}
            {hits.data?.length === 0 && <div className="p-2 text-sm text-slate-500">검색 결과가 없습니다.</div>}
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {hits.data?.map((h) => (
                <li key={h.corp_code}>
                  <button
                    onClick={() => pick(h)}
                    disabled={create.isPending}
                    className="flex w-full items-center justify-between px-2 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800"
                  >
                    <span>
                      <span className="font-medium">{h.corp_name}</span>
                      <span className="tabular ml-2 text-xs text-slate-500">{h.stock_code ?? '비상장'} · 고유번호 {h.corp_code}</span>
                    </span>
                    {h.existing_project_id ? <Badge tone="blue">등록됨 · 열기</Badge> : <span className="text-xs text-blue-600">선택</span>}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate({ manual: { name: mName, stock_code: mCode || undefined, market: mMarket } })
          }}
        >
          <Notice tone="amber">수동 등록 기업은 공식자료로 검증되지 않은 상태로 표시됩니다. DART 연동 후 다시 검증하는 것을 권장합니다.</Notice>
          <label className="block text-sm">
            기업명
            <input required value={mName} onChange={(e) => setMName(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950" />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm">
              종목코드 (6자리)
              <input value={mCode} onChange={(e) => setMCode(e.target.value)} pattern="\d{6}" className="tabular mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950" />
            </label>
            <label className="block text-sm">
              시장
              <select value={mMarket} onChange={(e) => setMMarket(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950">
                {['KOSPI', 'KOSDAQ', 'KONEX', 'UNLISTED', 'FOREIGN', 'UNKNOWN'].map((m) => (
                  <option key={m} value={m}>{MARKET_LABELS[m]}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex justify-end">
            <Button variant="primary" type="submit" disabled={create.isPending}>등록</Button>
          </div>
        </form>
      )}

      {err && (
        <div className="mt-3">
          <Notice tone={existingId ? 'blue' : 'red'}>
            {err.message}
            {existingId && (
              <button className="ml-2 font-semibold underline" onClick={() => { onClose(); nav(`/projects/${existingId}`) }}>
                기존 프로젝트 열기
              </button>
            )}
          </Notice>
        </div>
      )}
    </Modal>
  )
}

export default function Dashboard() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const dq = useDebounced(q.trim())
  const status = useQuery({ queryKey: ['status'], queryFn: api.status })
  const projects = useQuery({ queryKey: ['projects', dq], queryFn: () => api.projects(dq || undefined) })

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">고객사 프로젝트</h1>
          <p className="mt-1 text-sm text-slate-500">고객사별 독립 분석 작업공간 — 동종업계 확정, 데이터 수집·분석, 결과, 이력</p>
        </div>
        <div className="flex gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="고객사 검색"
            className="w-56 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900"
          />
          <Button variant="primary" onClick={() => setOpen(true)}>+ 새 고객사 등록</Button>
        </div>
      </div>

      {status.data && !status.data.database_ok && (
        <div className="mt-6"><Notice tone="red">PostgreSQL에 연결할 수 없습니다. scripts\setup_db.ps1로 DB를 설정하세요.</Notice></div>
      )}

      <div className="mt-6">
        {projects.isLoading && <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> 불러오는 중…</div>}
        {projects.isError && <Notice tone="red">{(projects.error as Error).message}</Notice>}
        {projects.data?.length === 0 && (
          <Empty title={dq ? '검색 결과가 없습니다' : '등록된 고객사가 없습니다'}>
            {!dq && '‘새 고객사 등록’에서 기업명 또는 종목코드로 고객사를 검색해 프로젝트를 만드세요.'}
          </Empty>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {projects.data?.map((p) => <ProjectCard key={p.id} p={p} />)}
        </div>
      </div>
      {open && <RegisterModal open={open} onClose={() => setOpen(false)} dartReady={!!status.data?.integrations.dart} />}
    </div>
  )
}
