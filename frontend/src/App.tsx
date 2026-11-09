import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import ProjectLayout, { ProjectIndexRedirect } from './pages/ProjectLayout'
import { CompanyDetail, Step3Results } from './pages/Results'
import Step1Peers from './pages/Step1Peers'
import { Step2Analysis, Step4History } from './pages/StepsAnalysis'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<ProjectIndexRedirect />} />
          <Route path="peers" element={<Step1Peers />} />
          <Route path="analysis" element={<Step2Analysis />} />
          <Route path="results" element={<Step3Results />} />
          <Route path="results/company/:companyId" element={<CompanyDetail />} />
          <Route path="history" element={<Step4History />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
