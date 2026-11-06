// Render smoke test of the results dashboard with a results payload supplied via RESULTS_FIXTURE
// (a local, git-ignored file path). Skipped when not provided — no financial data is committed.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import ProjectLayout from '../pages/ProjectLayout'
import { CompanyDetail, Step3Results } from '../pages/Results'

const fixturePath = process.env.RESULTS_FIXTURE

describe.skipIf(!fixturePath)('results dashboard render (real payload)', () => {
  const data = fixturePath ? JSON.parse(readFileSync(fixturePath, 'utf8')) : null

  function setup(path: string) {
    globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const u = String(url)
      let body: unknown = {}
      if (u.match(/\/api\/projects\/[^/]+$/)) body = data.project
      else if (u.includes('/results')) body = data.results
      else if (u.includes('/companies/')) {
        const v = data.results.companies.find((c: { is_target: boolean }) => !c.is_target)
        body = { valuation: { ...data.results.target, ...v, name: v.name, is_target: false }, target: data.results.target, company: {} }
      }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="projects/:projectId" element={<ProjectLayout />}>
              <Route path="results" element={<Step3Results />} />
              <Route path="results/company/:companyId" element={<CompanyDetail />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
  }

  it('renders verdict, KPIs, report sections and peer table without crashing', async () => {
    const pid = data.project.id
    setup(`/projects/${pid}/results?run=${data.results.run.id}`)
    await waitFor(() => expect(screen.getByText('현재 주가와 적정가치 범위')).toBeInTheDocument(), { timeout: 10000 })
    expect(screen.getAllByText(data.results.run.summary.verdict).length).toBeGreaterThan(0)
    expect(screen.getByText('근거 기반 AI 분석 보고서')).toBeInTheDocument()
    expect(screen.getAllByText(/⑧ 평가 변화 조건/).length).toBeGreaterThan(0)
    expect(screen.getByText('동종업계 비교표 — 기업을 클릭하면 상세 분석')).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })

  it('renders peer company detail page', async () => {
    const pid = data.project.id
    setup(`/projects/${pid}/results/company/2?run=${data.results.run.id}`)
    await waitFor(() => expect(screen.getByText('적정가치 분석 (같은 계산 엔진)')).toBeInTheDocument(), { timeout: 10000 })
  })
})
