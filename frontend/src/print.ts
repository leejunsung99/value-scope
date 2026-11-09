// 인쇄용 보고서(/print/...) 화면 여부. 인쇄 화면은 항상 새 창(또는 PDF 변환용 headless 브라우저)으로 열리므로 상수로 충분하다.
export const IS_PRINT = typeof window !== 'undefined' && window.location.pathname.startsWith('/print/')

export type ReportKind = 'detail' | 'brief'

export function printUrl(projectId: string, runId: string, autoprint = false, kind: ReportKind = 'detail') {
  return `/print/projects/${projectId}/runs/${runId}${kind === 'brief' ? '/brief' : ''}${autoprint ? '?autoprint=1' : ''}`
}
