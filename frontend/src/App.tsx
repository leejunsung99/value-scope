import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import ProjectLayout from './pages/ProjectLayout'
import Step1Peers from './pages/Step1Peers'
import { Step2Analysis, Step3Results, Step4History } from './pages/StepsPending'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<Navigate to="peers" replace />} />
          <Route path="peers" element={<Step1Peers />} />
          <Route path="analysis" element={<Step2Analysis />} />
          <Route path="results" element={<Step3Results />} />
          <Route path="history" element={<Step4History />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
