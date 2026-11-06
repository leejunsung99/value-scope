import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Badge, Button, Card, CATEGORY_LABELS, Empty, fmtDateTime, Notice, USE_TYPE_LABELS } from '../ui'
import { useProject } from './ProjectLayout'

export function Step2Analysis() {
  const { project } = useProject()
  const no = project.peer_state.latest_version_no
  const v = useQuery({ queryKey: ['version', project.id, no], queryFn: () => api.version(project.id, no), enabled: no > 0 })

  if (!no) {
    return (
      <Empty title="확정된 비교군이 없습니다">
        분석은 고객사와 확정된 비교기업 전체를 대상으로 실행됩니다. 먼저{' '}
        <Link to="../peers" className="text-blue-600 underline">STEP 1</Link>에서 비교기업을 채택하고 ‘비교기업 확정’을 누르세요.
      </Empty>
    )
  }
  return (
    <div className="space-y-4">
      {project.peer_state.draft_dirty && (
        <Notice>STEP 1 작업본이 마지막 확정본(v{no})과 다릅니다. 변경 내용을 분석에 반영하려면 STEP 1에서 다시 확정하세요.</Notice>
      )}
      <Card className="p-5">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">분석 대상 — {v.data?.label ?? `비교군 v${no}`}</div>
            <div className="text-xs text-slate-500">확정 {fmtDateTime(v.data?.confirmed_at)} · 고객사 + 비교기업 {v.data?.member_count ?? '-'}개</div>
          </div>
          <Button variant="primary" disabled title="PHASE 2에서 구현 예정">기업 분석 실행</Button>
        </div>
        <Notice tone="slate">
          <b>구현 예정 (PHASE 2)</b> — DART 재무제표·공시·수주, KRX 주가 증분 수집과 가치평가 엔진은 아직 구현되지 않았습니다.
          실제 데이터 연동 전에는 어떤 분석값도 표시하지 않습니다.
        </Notice>
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr><th className="py-1.5">기업</th><th>카테고리</th><th>활용 구분</th><th className="text-right">적합성 점수</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {v.data?.members?.map((m) => (
              <tr key={m.company_id}>
                <td className="py-1.5">{m.snapshot.company.name} <span className="tabular text-xs text-slate-500">{m.snapshot.company.stock_code ?? ''}</span></td>
                <td><Badge>{m.category}. {CATEGORY_LABELS[m.category]}</Badge></td>
                <td>{USE_TYPE_LABELS[m.use_type]}</td>
                <td className="tabular text-right">{m.score?.toFixed(1) ?? '평가 보류'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

export function Step3Results() {
  return (
    <Empty title="분석 결과가 없습니다">
      STEP 2에서 분석을 실행하면 종합 평가, 적정가치 시나리오, 그래프, 근거 기반 보고서가 이곳에 표시됩니다. (PHASE 3 구현 예정)
    </Empty>
  )
}

export function Step4History() {
  const { project } = useProject()
  const peers = useQuery({ queryKey: ['peers', project.id], queryFn: () => api.peers(project.id) })
  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="text-sm font-semibold">비교군 확정 이력</div>
        {peers.data?.versions.length ? (
          <ul className="mt-3 divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {[...peers.data.versions].reverse().map((v) => (
              <li key={v.id} className="flex items-center justify-between py-2">
                <span><Badge tone="blue">v{v.version_no}</Badge> <span className="ml-2">{v.label}</span>{v.note && <span className="ml-2 text-xs text-slate-500">— {v.note}</span>}</span>
                <span className="text-xs text-slate-500">{fmtDateTime(v.confirmed_at)} · {v.member_count}개 기업</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="mt-2 text-sm text-slate-500">확정된 비교군이 없습니다.</div>
        )}
      </Card>
      <Notice tone="slate">분석 Snapshot 이력과 과거·최신 분석 비교는 PHASE 4에서 구현 예정입니다.</Notice>
    </div>
  )
}
