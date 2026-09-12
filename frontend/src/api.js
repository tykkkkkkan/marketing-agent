import axios from 'axios'

// 统一走 /api（开发环境由 vite proxy 转发到 FastAPI:8010）
// timeout 设长：文案生成要等大模型调工具 + 生成
const http = axios.create({ baseURL: '/api', timeout: 180000 })

const unwrap = (p) => p.then((r) => r.data)

export const api = {
  // ── 系统 ──
  health: () => unwrap(http.get('/health')),
  llmStatus: () => unwrap(http.get('/marketing/llm-status')),

  // ── 经营分析（只读 ZT-agent 数据）──
  overview: () => unwrap(http.get('/analytics/overview')),
  diagnosis: () => unwrap(http.get('/analytics/diagnosis')),
  salesTop: (limit = 5, days = 90) =>
    unwrap(http.get('/analytics/sales-top', { params: { limit, days } })),
  lowStock: (limit = 10) => unwrap(http.get('/analytics/low-stock', { params: { limit } })),
  hotQuestions: (limit = 8) =>
    unwrap(http.get('/analytics/hot-questions', { params: { limit } })),

  // ── 营销自动化 ──
  generate: (payload) => unwrap(http.post('/marketing/generate', payload)),
  drafts: (status = '') => unwrap(http.get('/marketing/drafts', { params: { status } })),
  review: (id, payload) => unwrap(http.post(`/marketing/drafts/${id}/review`, payload)),
  removeDraft: (id) => unwrap(http.delete(`/marketing/drafts/${id}`)),
  stats: () => unwrap(http.get('/marketing/stats')),

  // ── 运营执行层（让 Agent 真正去办事：补货 / 发货 / 售后）──
  actions: () => unwrap(http.get('/operations/actions')),
  policy: () => unwrap(http.get('/operations/policy')),
  updatePolicy: (payload) => unwrap(http.put('/operations/policy', payload)),
  killSwitch: (on) => unwrap(http.post('/operations/kill-switch', { on, actor: '运营-汤' })),
  ztStatus: () => unwrap(http.get('/operations/zt-status')),
  scanPreview: () => unwrap(http.get('/operations/scan-preview')),
  scan: (actor = '运营-汤') => unwrap(http.post('/operations/scan', null, { params: { actor } })),
  tasks: (status = '', actionCode = '') =>
    unwrap(http.get('/operations/tasks', { params: { status, action_code: actionCode } })),
  task: (id) => unwrap(http.get(`/operations/tasks/${id}`)),
  createTask: (payload) => unwrap(http.post('/operations/tasks', payload)),
  patchTask: (id, payload) => unwrap(http.patch(`/operations/tasks/${id}`, { payload })),
  approveTask: (id, note = '') =>
    unwrap(http.post(`/operations/tasks/${id}/approve`, { actor: '运营-汤', note, execute_now: true })),
  rejectTask: (id, note = '') =>
    unwrap(http.post(`/operations/tasks/${id}/reject`, { actor: '运营-汤', note, execute_now: false })),
  cancelTask: (id, note = '') =>
    unwrap(http.post(`/operations/tasks/${id}/cancel`, { actor: '运营-汤', note, execute_now: false })),
  opsStats: () => unwrap(http.get('/operations/stats')),
  opsAudit: (limit = 30) => unwrap(http.get('/operations/audit', { params: { limit } })),
  // 自动运营（无人值守）
  autoPilot: () => unwrap(http.get('/operations/auto-pilot')),
  autoPilotRun: (trigger = 'manual') =>
    unwrap(http.post('/operations/auto-pilot/run', null, { params: { trigger } })),
}
