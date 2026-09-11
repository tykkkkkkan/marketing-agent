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
}
