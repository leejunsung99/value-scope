// STEP navigation flow with synthetic API responses (no real financial data).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ProjectLayout, { ProjectIndexRedirect } from '../pages/ProjectLayout'
import Step1Peers from '../pages/Step1Peers'
import { Step2Analysis } from '../pages/StepsAnalysis'

const RUN = {
  id: 'run-1', project_id: 'p1', status: 'succeeded', started_at: '2026-11-06T09:00:00Z', finished_at: '2026-11-06T09:04:00Z',
  peer_version_no: 1, price_as_of: '20261105', model_version: 'x', ai_provider: 'none', ai_model: null,
  summary: { verdict: '판단 유보', fair_value: { conservative: 1, base: 2, optimistic: 3 }, price: 2 }, progress: { companies: {} }, messages: [],
}

function project(withResult: boolean) {
  return {
    id: 'p1', title: 'T', current_step: withResult ? 3 : 2, step_label: '', created_at: '', updated_at: '',
    last_analysis_at: withResult ? RUN.finished_at : null, last_analysis_status: withResult ? 'succeeded' : null,
    company: { id: 1, name: '테스트기업', stock_code: '000001', market: 'KOSDAQ', is_verified: true },
    peer_state: {
      latest_version_no: 1, draft_dirty: false, included_count: 3, last_search: null,
      last_run: withResult ? { id: 'run-1', status: 'succeeded', started_at: RUN.started_at, peer_version_no: 1 } : null,
      latest_result_run_id: withResult ? 'run-1' : null, needs_reanalysis: !withResult,
    },
  }
}

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname}{loc.search}</div>
}

function setup(path: string, withResult: boolean) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url)
    let body: unknown = {}
    if (u === '/api/projects/p1') body = project(withResult)
    else if (u.endsWith('/analysis-runs')) body = withResult ? [RUN] : []
    else if (u.includes('/analysis-runs/run-1')) body = RUN
    else if (u.includes('/api-usage')) body = { DART: { used_today: 0, limit: 3000 }, KRX: { used_today: 0, limit: 1000 } }
    else if (u.includes('/peers/versions/')) body = { id: 1, version_no: 1, label: 'v1', note: null, confirmed_at: '', member_count: 0, members: [] }
    else if (u.endsWith('/peers')) body = { group_id: 1, latest_version_no: 1, draft_dirty: false, updated_at: '', members: [], versions: [] }
    else if (u.includes('/peer-search/sessions')) body = []
    else if (u.includes('/rubric')) body = { version: 'v', min_coverage: 0.5, criteria: [], note: '' }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="projects/:projectId" element={<ProjectLayout />}>
            <Route index element={<ProjectIndexRedirect />} />
            <Route path="peers" element={<Step1Peers />} />
            <Route path="analysis" element={<Step2Analysis />} />
            <Route path="results" element={<div>RESULTS PAGE</div>} />
          </Route>
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('STEP flow', () => {
  it('opening a project with results lands on STEP 3', async () => {
    setup('/projects/p1', true)
    await waitFor(() => expect(screen.getByText('RESULTS PAGE')).toBeInTheDocument())
    expect(screen.getByText(/결과 있음/)).toBeInTheDocument()
  })

  it('opening a confirmed project without results lands on STEP 2', async () => {
    setup('/projects/p1', false)
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/projects/p1/analysis'))
    expect(screen.getByText(/실행 필요/)).toBeInTheDocument()
  })

  it('STEP 2 shows a next-step button that goes to STEP 3', async () => {
    setup('/projects/p1/analysis', true)
    const btn = await screen.findByRole('button', { name: /STEP 3 결과 보기/ })
    fireEvent.click(btn)
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/projects/p1/results?run=run-1'))
  })

  it('STEP 1 offers moving on to STEP 2 once confirmed', async () => {
    setup('/projects/p1/peers', true)
    const btn = await screen.findByRole('button', { name: /다음: STEP 2 분석/ })
    fireEvent.click(btn)
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/projects/p1/analysis'))
  })
})
