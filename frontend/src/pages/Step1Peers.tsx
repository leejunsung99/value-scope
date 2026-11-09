import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError, type Category, type Member, type MemberStatus, type PeerGroup, type UseType } from '../api'
import {
  Badge,
  Button,
  Card,
  CATEGORY_LABELS,
  Empty,
  fmtDate,
  fmtDateTime,
  MARKET_LABELS,
  Modal,
  Notice,
  ORIGIN_LABELS,
  ScoreBar,
  Spinner,
  USE_TYPE_LABELS,
  VERIFICATION_LABELS,
} from '../ui'
import { useProject } from './ProjectLayout'

const CATS = Object.keys(CATEGORY_LABELS) as Category[]
const STATUS_LABEL: Record<MemberStatus, string> = { proposed: '추천(미채택)', included: '채택', excluded: '제외' }

function selectCls() {
  return 'rounded border border-slate-300 bg-white px-1.5 py-1 text-xs dark:border-slate-700 dark:bg-slate-950'
}

// ---------------------------------------------------------------- detail modal
function DetailModal({ m, onClose }: { m: Member; onClose: () => void }) {
  const c = m.candidate
  const ver = VERIFICATION_LABELS[c?.verification_status ?? (m.company.is_verified ? 'verified' : 'unverified')]
  return (
    <Modal open onClose={onClose} title={`${m.company.name} — 비교 적합성 근거`} wide>
      <div className="grid grid-cols-3 gap-4 text-sm">
        <div className="col-span-2 space-y-3">
          <Field label="주요 사업">{c?.main_business}</Field>
          <Field label="주요 제품">{c?.main_products}</Field>
          <Field label="고객사와의 사업 유사성">{c?.similarity}</Field>
          <Field label="카테고리 선정 이유" pre>{c?.selection_reason}</Field>
          <Field label="비교 시 주의점" pre>{c?.caution}</Field>
        </div>
        <Card className="space-y-1.5 p-3 text-xs">
          <Row k="종목코드" v={m.company.stock_code ?? '없음'} />
          <Row k="DART 고유번호" v={m.company.corp_code ?? '없음'} />
          <Row k="시장" v={MARKET_LABELS[m.company.market] ?? m.company.market} />
          <Row k="상장 여부" v={m.company.is_listed === null ? '확인 불가' : m.company.is_listed ? '상장' : '비상장'} />
          <Row k="업종코드" v={m.company.induty_code ?? '—'} />
          <Row k="출처 구분" v={ORIGIN_LABELS[c?.origin ?? m.added_by] ?? m.added_by} />
          <div className="pt-1"><Badge tone={ver.tone}>{ver.label}</Badge></div>
          <div className="whitespace-pre-line text-slate-500">{c?.verification_note ?? m.company.verification_note}</div>
        </Card>
      </div>

      <h3 className="mt-6 text-sm font-semibold">비교 적합성 점수 산출 근거</h3>
      {c ? (
        <>
          <div className="mt-1 flex items-center gap-3 text-xs text-slate-500">
            <ScoreBar score={c.score} coverage={c.score_coverage} />
            <span>점수 = Σ(가중치 × 평가수준) ÷ Σ(평가된 항목 가중치) × 100 · 미평가 항목은 0점이 아니라 분모에서 제외</span>
          </div>
          <table className="mt-2 w-full text-xs">
            <thead className="bg-slate-50 text-left text-slate-500 dark:bg-slate-800/60">
              <tr><th className="px-2 py-1.5">항목</th><th>가중치</th><th>평가</th><th>근거</th><th>판단 주체</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {c.score_breakdown.map((b) => (
                <tr key={b.key} className="align-top">
                  <td className="px-2 py-1.5 font-medium">{b.label}</td>
                  <td className="tabular">{b.weight}</td>
                  <td>{b.level === null ? <span className="text-slate-400">미평가</span> : `${b.level_label} (${b.level})`}</td>
                  <td className="max-w-md py-1.5">
                    {b.rationale}
                    {b.evidence?.map((e, i) => <div key={i} className="mt-1 border-l-2 border-blue-300 pl-2 text-slate-500">“{e}”</div>)}
                  </td>
                  <td>{b.origin === 'ai' ? <Badge tone="violet">AI 판단</Badge> : b.origin === 'rule' ? <Badge>규칙</Badge> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <div className="mt-1 text-xs text-slate-500">사용자가 직접 추가한 기업이라 검색 근거가 없습니다. 재검색 시 근거가 수집될 수 있습니다.</div>
      )}

      <h3 className="mt-6 text-sm font-semibold">관련 공식 공시 및 출처</h3>
      {c?.sources.length ? (
        <ul className="mt-2 space-y-2 text-xs">
          {c.sources.map((s, i) => (
            <li key={i} className="rounded border border-slate-200 p-2 dark:border-slate-800">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="blue">{s.type}</Badge>
                {s.url ? <a href={s.url} target="_blank" rel="noreferrer" className="font-medium text-blue-600 hover:underline">{s.title}</a> : <span>{s.title}</span>}
                {s.rcept_no && <span className="tabular text-slate-500">접수번호 {s.rcept_no}</span>}
                {s.filed_date && <span className="text-slate-500">{fmtDate(s.filed_date)}</span>}
                {s.section && <span className="text-slate-500">· {s.section}</span>}
                {s.note && <span className="text-slate-500">· {s.note}</span>}
              </div>
              {s.quote && <div className="mt-1.5 border-l-2 border-slate-300 pl-2 text-slate-600 dark:text-slate-400">“…{s.quote}…”</div>}
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-1 text-xs text-slate-500">연결된 공식 출처가 없습니다.</div>
      )}
    </Modal>
  )
}

function Field({ label, children, pre }: { label: string; children: React.ReactNode; pre?: boolean }) {
  return (
    <div>
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className={`mt-0.5 ${pre ? 'whitespace-pre-line' : ''}`}>{children || <span className="text-slate-400">데이터 없음</span>}</div>
    </div>
  )
}
function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between gap-2"><span className="text-slate-500">{k}</span><span className="tabular text-right">{v}</span></div>
}

// ---------------------------------------------------------------- add modal
function AddModal({ projectId, onClose, onDone }: { projectId: string; onClose: () => void; onDone: (g: PeerGroup) => void }) {
  const [q, setQ] = useState('')
  const [cat, setCat] = useState<Category>('A')
  const [manual, setManual] = useState(false)
  const [mName, setMName] = useState('')
  const [mCountry, setMCountry] = useState('KR')
  const hits = useQuery({ queryKey: ['company-search-listed', q], queryFn: () => api.searchCompanies(q, true), enabled: !manual && q.trim().length > 0 })
  const add = useMutation({ mutationFn: (b: Parameters<typeof api.addPeer>[1]) => api.addPeer(projectId, b), onSuccess: (g) => { onDone(g); onClose() } })
  return (
    <Modal open onClose={onClose} title="비교기업 직접 추가">
      <div className="mb-3 flex items-center gap-2 text-sm">
        카테고리
        <select value={cat} onChange={(e) => setCat(e.target.value as Category)} className={selectCls()}>
          {CATS.map((k) => <option key={k} value={k}>{k}. {CATEGORY_LABELS[k]}</option>)}
        </select>
        <label className="ml-auto flex items-center gap-1 text-xs"><input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} /> DART 미등록(해외·비상장) 기업</label>
      </div>
      {!manual ? (
        <>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="상장사 기업명 또는 종목코드" className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950" />
          {hits.isFetching && <div className="mt-2 text-xs text-slate-500"><Spinner className="h-3 w-3" /> 검색 중…</div>}
          {hits.isError && <div className="mt-2"><Notice tone="red">{(hits.error as Error).message}</Notice></div>}
          <ul className="mt-2 max-h-72 divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800">
            {hits.data?.map((h) => (
              <li key={h.corp_code}>
                <button disabled={add.isPending} onClick={() => add.mutate({ corp_code: h.corp_code, category: cat })} className="flex w-full justify-between px-2 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800">
                  <span>{h.corp_name} <span className="tabular text-xs text-slate-500">{h.stock_code}</span></span>
                  <span className="text-xs text-blue-600">추가</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate({ manual_name: mName, manual_country: mCountry, category: cat }) }}>
          <Notice tone="amber">DART로 검증할 수 없는 기업은 ‘미검증’으로 표시되며 적정가치 비교용으로 지정할 수 없습니다.</Notice>
          <div className="grid grid-cols-3 gap-2">
            <input required value={mName} onChange={(e) => setMName(e.target.value)} placeholder="기업명" className="col-span-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950" />
            <input required value={mCountry} onChange={(e) => setMCountry(e.target.value.toUpperCase())} maxLength={2} placeholder="국가(JP)" className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950" />
          </div>
          <div className="flex justify-end"><Button type="submit" variant="primary" disabled={add.isPending}>추가</Button></div>
        </form>
      )}
      {add.isError && <div className="mt-3"><Notice tone="red">{(add.error as Error).message}</Notice></div>}
    </Modal>
  )
}

// ---------------------------------------------------------------- confirm modal
function ConfirmModal({ group, projectId, onClose, onDone }: { group: PeerGroup; projectId: string; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState('')
  const included = group.members.filter((m) => m.status === 'included')
  const confirm = useMutation({ mutationFn: () => api.confirmPeers(projectId, note || undefined), onSuccess: () => { onDone(); onClose() } })
  return (
    <Modal open onClose={onClose} title={`비교군 v${group.latest_version_no + 1} 확정`}>
      <p className="text-sm text-slate-600 dark:text-slate-300">채택된 {included.length}개 기업과 카테고리·활용 구분을 새 버전으로 고정합니다. 확정된 버전은 이후 수정해도 변경되지 않습니다.</p>
      <ul className="mt-3 max-h-60 overflow-y-auto text-sm">
        {included.map((m) => (
          <li key={m.id} className="flex justify-between py-1"><span>{m.company.name}</span><span className="text-xs text-slate-500">{m.category} · {USE_TYPE_LABELS[m.use_type]}</span></li>
        ))}
      </ul>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="확정 메모 (선택)" className="mt-3 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950" rows={2} />
      {confirm.isError && <div className="mt-2"><Notice tone="red">{(confirm.error as Error).message}</Notice></div>}
      <div className="mt-3 flex justify-end gap-2">
        <Button onClick={onClose}>취소</Button>
        <Button variant="primary" disabled={confirm.isPending || !included.length} onClick={() => confirm.mutate()}>확정하고 STEP 2로</Button>
      </div>
    </Modal>
  )
}

// ---------------------------------------------------------------- main
export default function Step1Peers() {
  const { project } = useProject()
  const pid = project.id
  const qc = useQueryClient()
  const nav = useNavigate()
  const [detail, setDetail] = useState<Member | null>(null)
  const [adding, setAdding] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [statusFilter, setStatusFilter] = useState<'all' | MemberStatus>('all')
  const [showLog, setShowLog] = useState(true)

  const peers = useQuery({ queryKey: ['peers', pid], queryFn: () => api.peers(pid) })
  const sessions = useQuery({
    queryKey: ['sessions', pid],
    queryFn: () => api.searchSessions(pid),
    refetchInterval: (q) => (q.state.data?.[0]?.status === 'running' ? 2000 : false),
  })
  const latest = sessions.data?.[0]
  const running = latest?.status === 'running'
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) {
      qc.invalidateQueries({ queryKey: ['peers', pid] })
      qc.invalidateQueries({ queryKey: ['project', pid] })
    }
    wasRunning.current = running
  }, [running, pid, qc])

  const setGroup = (g: PeerGroup) => {
    qc.setQueryData(['peers', pid], g)
    qc.invalidateQueries({ queryKey: ['project', pid] })
  }
  const start = useMutation({
    mutationFn: () => api.startPeerSearch(pid),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ['sessions', pid] }), 300),
  })
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.patchPeer>[2] }) => api.patchPeer(pid, id, body),
    onSuccess: setGroup,
  })
  const bulk = useMutation({ mutationFn: ({ ids, status }: { ids: number[]; status: MemberStatus }) => api.bulkStatus(pid, ids, status), onSuccess: setGroup })

  const g = peers.data
  const counts = useMemo(() => {
    const c = { proposed: 0, included: 0, excluded: 0 }
    g?.members.forEach((m) => c[m.status]++)
    return c
  }, [g])
  const summary = latest?.sources_used.find((s) => s.type === 'AI_SUMMARY')?.summary
  const targetDoc = latest?.sources_used.find((s) => s.type === 'DART')
  const err = (patch.error ?? bulk.error ?? start.error) as ApiError | null

  return (
    <div className="space-y-4">
      {/* toolbar */}
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <Button variant="primary" onClick={() => start.mutate()} disabled={running || start.isPending}>
          {running ? <><Spinner /> 동종업계 검색 중…</> : g?.members.length ? '동종업계 재검색' : '동종업계 검색'}
        </Button>
        <div className="text-xs text-slate-500">
          {latest ? <>최근 검색 {fmtDateTime(latest.started_at)} · <SessionBadge status={latest.status} /> · AI {latest.ai_provider ?? 'none'}{latest.ai_model ? ` (${latest.ai_model})` : ''}</> : '검색 이력 없음 — DART 사업보고서와 초기 후보 목록으로 후보를 찾습니다.'}
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <span className="tabular">채택 <b>{counts.included}</b> · 추천 <b>{counts.proposed}</b> · 제외 <b>{counts.excluded}</b></span>
          <Button size="sm" onClick={() => setAdding(true)}>+ 직접 추가</Button>
          <Button variant="primary" size="sm" disabled={!counts.included || (!g?.draft_dirty && (g?.latest_version_no ?? 0) > 0)} onClick={() => setConfirming(true)}
            title={!g?.draft_dirty && g?.latest_version_no ? '마지막 확정본과 변경 사항이 없습니다' : ''}>
            비교기업 확정
          </Button>
          {(g?.latest_version_no ?? 0) > 0 && !g?.draft_dirty && (
            <Button size="sm" onClick={() => nav(`/projects/${pid}/analysis`)}>다음: STEP 2 분석 →</Button>
          )}
        </div>
      </Card>

      {g && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          {g.latest_version_no > 0 ? <Badge tone="blue">최근 확정 v{g.latest_version_no}</Badge> : <Badge>미확정</Badge>}
          {g.draft_dirty ? <Badge tone="amber">임시 편집 중 — 확정본과 다름 (자동 저장됨)</Badge> : g.latest_version_no > 0 && <Badge tone="green">확정본과 동일</Badge>}
          <span>작업본 저장 {fmtDateTime(g.updated_at)}</span>
        </div>
      )}

      {err && <Notice tone="red">{err.message}</Notice>}

      {/* search log */}
      {latest && latest.messages.length > 0 && (
        <Card className="p-4">
          <button className="flex w-full items-center justify-between text-left text-sm font-semibold" onClick={() => setShowLog(!showLog)}>
            검색 실행 로그 및 데이터 출처 <span className="text-xs font-normal text-slate-500">{showLog ? '접기' : '펼치기'}</span>
          </button>
          {showLog && (
            <div className="mt-2 space-y-1">
              {targetDoc && (
                <div className="text-xs">
                  고객사 근거 공시:{' '}
                  <a href={targetDoc.url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{targetDoc.title}</a>
                  <span className="tabular ml-1 text-slate-500">(접수번호 {targetDoc.rcept_no}, {fmtDate(targetDoc.filed_date)})</span>
                </div>
              )}
              {latest.messages.map((m, i) => (
                <div key={i} className={`text-xs ${m.level === 'error' ? 'text-red-600' : m.level === 'warning' ? 'text-amber-700 dark:text-amber-400' : 'text-slate-600 dark:text-slate-400'}`}>
                  {m.level === 'error' ? '✕' : m.level === 'warning' ? '!' : '·'} {m.message}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {summary && (
        <Card className="p-4">
          <div className="flex items-center gap-2 text-sm font-semibold">고객사 사업 요약 <Badge tone="violet">AI 판단 · 공시 기반</Badge></div>
          <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {[['main_business', '주요 사업'], ['main_products', '주요 제품'], ['customers', '주요 고객'], ['revenue_structure', '매출 구조']].map(([k, l]) => (
              <div key={k}><dt className="text-xs text-slate-500">{l}</dt><dd>{summary[k] || '데이터 없음'}</dd></div>
            ))}
          </dl>
        </Card>
      )}

      {/* filter */}
      {g && g.members.length > 0 && (
        <div className="flex items-center gap-1 text-xs">
          {(['all', 'included', 'proposed', 'excluded'] as const).map((s) => (
            <button key={s} onClick={() => setStatusFilter(s)} className={`rounded px-2 py-1 ${statusFilter === s ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'}`}>
              {s === 'all' ? '전체' : STATUS_LABEL[s]}
            </button>
          ))}
        </div>
      )}

      {peers.isLoading && <div className="text-sm text-slate-500"><Spinner /> 불러오는 중…</div>}
      {g && g.members.length === 0 && !running && (
        <Empty title="아직 동종업계 후보가 없습니다">‘동종업계 검색’을 누르면 DART 사업보고서와 초기 후보 목록을 바탕으로 후보를 찾고, 카테고리별로 분류해 근거와 함께 보여줍니다.</Empty>
      )}

      {g && CATS.map((cat) => {
        const rows = g.members.filter((m) => m.category === cat && (statusFilter === 'all' || m.status === statusFilter))
        if (!rows.length) return null
        const proposedIds = rows.filter((m) => m.status === 'proposed').map((m) => m.id)
        return (
          <Card key={cat}>
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
              <div className="text-sm font-semibold"><span className="mr-1.5 text-blue-600">{cat}</span>{CATEGORY_LABELS[cat]} <span className="ml-1 text-xs font-normal text-slate-500">{rows.length}</span></div>
              {proposedIds.length > 0 && <Button size="sm" variant="ghost" onClick={() => bulk.mutate({ ids: proposedIds, status: 'included' })}>추천 {proposedIds.length}개 모두 채택</Button>}
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2 font-medium">기업</th>
                  <th className="font-medium">시장</th>
                  <th className="font-medium">출처·검증</th>
                  <th className="w-[32%] font-medium">주요 사업 / 선정 이유</th>
                  <th className="font-medium">카테고리 · 활용 구분</th>
                  <th className="font-medium">적합성</th>
                  <th className="pr-4 text-right font-medium">상태</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((m) => {
                  const c = m.candidate
                  const ver = VERIFICATION_LABELS[c?.verification_status ?? (m.company.is_verified ? 'verified' : 'unverified')]
                  return (
                    <tr key={m.id} className={`align-top ${m.status === 'excluded' ? 'opacity-50' : ''}`}>
                      <td className="px-4 py-2.5">
                        <button className="font-medium text-slate-900 hover:text-blue-600 hover:underline dark:text-slate-100" onClick={() => setDetail(m)}>{m.company.name}</button>
                        <div className="tabular text-xs text-slate-500">{m.company.stock_code ?? '—'}</div>
                      </td>
                      <td className="py-2.5 text-xs">{MARKET_LABELS[m.company.market] ?? m.company.market}</td>
                      <td className="space-y-1 py-2.5">
                        <div><Badge tone={m.added_by === 'seed' ? 'slate' : m.added_by === 'user' ? 'blue' : 'violet'}>{ORIGIN_LABELS[c?.origin ?? m.added_by] ?? m.added_by}</Badge></div>
                        <div><Badge tone={ver.tone} title={c?.verification_note ?? ''}>{ver.label}</Badge></div>
                      </td>
                      <td className="py-2.5 pr-3 text-xs text-slate-600 dark:text-slate-400">
                        <div className="line-clamp-2">{c?.main_business ?? <span className="text-slate-400">데이터 없음</span>}</div>
                        {c?.selection_reason && <div className="mt-1 line-clamp-2 text-slate-500">{c.selection_reason}</div>}
                        <button className="mt-1 text-blue-600 hover:underline" onClick={() => setDetail(m)}>근거 상세보기</button>
                      </td>
                      <td className="space-y-1 py-2.5">
                        <select aria-label="카테고리" value={m.category} className={selectCls()} onChange={(e) => patch.mutate({ id: m.id, body: { category: e.target.value as Category } })}>
                          {CATS.map((k) => <option key={k} value={k}>{k}. {CATEGORY_LABELS[k]}</option>)}
                        </select>
                        <div>
                          <select aria-label="활용 구분" value={m.use_type} className={selectCls()} onChange={(e) => patch.mutate({ id: m.id, body: { use_type: e.target.value as UseType } })}>
                            {(Object.keys(USE_TYPE_LABELS) as UseType[]).map((k) => (
                              <option key={k} value={k} disabled={k === 'valuation' && !m.company.is_listed}>{USE_TYPE_LABELS[k]}</option>
                            ))}
                          </select>
                        </div>
                      </td>
                      <td className="py-2.5"><ScoreBar score={c?.score ?? null} coverage={c?.score_coverage ?? 0} /></td>
                      <td className="py-2.5 pr-4 text-right">
                        <div className="mb-1 text-xs">{m.status === 'included' ? <Badge tone="green">채택</Badge> : m.status === 'excluded' ? <Badge tone="red">제외</Badge> : <Badge tone="amber">추천</Badge>}</div>
                        {m.status !== 'included' && <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: m.id, body: { status: 'included' } })}>{m.status === 'excluded' ? '복원·채택' : '채택'}</Button>}
                        {m.status !== 'excluded' && <Button size="sm" variant="danger" onClick={() => patch.mutate({ id: m.id, body: { status: 'excluded' } })}>제외</Button>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        )
      })}

      <RubricInfo />

      {detail && <DetailModal m={g?.members.find((x) => x.id === detail.id) ?? detail} onClose={() => setDetail(null)} />}
      {adding && <AddModal projectId={pid} onClose={() => setAdding(false)} onDone={setGroup} />}
      {confirming && g && (
        <ConfirmModal group={g} projectId={pid} onClose={() => setConfirming(false)} onDone={() => {
          qc.invalidateQueries({ queryKey: ['peers', pid] })
          qc.invalidateQueries({ queryKey: ['project', pid] })
          nav(`/projects/${pid}/analysis`)
        }} />
      )}
    </div>
  )
}

function SessionBadge({ status }: { status: string }) {
  const map: Record<string, [string, 'blue' | 'green' | 'amber' | 'red']> = {
    running: ['진행 중', 'blue'], succeeded: ['완료', 'green'], partial: ['일부 완료', 'amber'], failed: ['실패', 'red'],
  }
  const [l, t] = map[status] ?? [status, 'blue']
  return <Badge tone={t}>{l}</Badge>
}

function RubricInfo() {
  const r = useQuery({ queryKey: ['rubric'], queryFn: api.rubric, staleTime: Infinity })
  const [open, setOpen] = useState(false)
  if (!r.data) return null
  return (
    <Card className="p-4 text-xs">
      <button className="text-sm font-semibold" onClick={() => setOpen(!open)}>비교 적합성 평가 기준 ({r.data.version}) {open ? '▴' : '▾'}</button>
      {open && (
        <>
          <p className="mt-2 text-slate-500">{r.data.note}</p>
          <table className="mt-2 w-full">
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {r.data.criteria.map((c) => (
                <tr key={c.key}><td className="py-1 font-medium">{c.label}</td><td className="tabular">{c.weight}점</td><td className="text-slate-500">{c.description}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  )
}
