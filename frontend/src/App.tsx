import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import BriefReport from './pages/BriefReport'
import PrintReport from './pages/PrintReport'
import ProjectHome from './pages/ProjectHome'
import ProjectLayout from './pages/ProjectLayout'
import { CompanyDetail, Step3Results } from './pages/Results'
import Step1Peers from './pages/Step1Peers'
import { Step2Analysis, Step4History } from './pages/StepsAnalysis'
import WeeklyPage from './pages/Weekly'

export default function App() {
  return (
    <Routes>
      <Route path="print/projects/:projectId/runs/:runId" element={<PrintReport />} />
      <Route path="print/projects/:projectId/runs/:runId/brief" element={<BriefReport />} />
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<ProjectHome />} />
          <Route path="peers" element={<Step1Peers />} />
          <Route path="analysis" element={<Step2Analysis />} />
          <Route path="results" element={<Step3Results />} />
          <Route path="results/company/:companyId" element={<CompanyDetail />} />
          <Route path="history" element={<Step4History />} />
          <Route path="weekly" element={<WeeklyPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
