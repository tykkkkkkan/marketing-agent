import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

const CHANNELS = ['朋友圈', '社群', '公众号', '短视频口播', '短信']
const TONES = ['促销', '专业', '温情', '幽默']
const STATUS_CLS = { 待审核: 'pill warn', 已通过: 'pill ok', 已驳回: 'pill bad' }

export default function App() {
  const [tab, setTab] = useState('board')

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">🐟 中渔小助</span>
          <span className="sub">营销自动化 Agent · 运营后台</span>
        </div>
        <HealthBadge />
      </header>

      <nav className="tabs">
        {[
          ['board', '经营看板'],
          ['generate', '文案生成'],
          ['review', '审核台'],
        ].map(([key, label]) => (
          <button
            key={key}
            className={tab === key ? 'tab active' : 'tab'}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="content">
        {tab === 'board' && <Board />}
        {tab === 'generate' && <Generator onGenerated={() => setTab('review')} />}
        {tab === 'review' && <ReviewDesk />}
      </main>

      <footer className="footer">
        数据来源：ZT-agent 的 agent_db（只读） · 本服务仅写入自管表 marketing_drafts
      </footer>
    </div>
  )
}

/* ─────────────── 后端状态 ─────────────── */
function HealthBadge() {
  const [state, setState] = useState({ ok: null, model: '' })

  useEffect(() => {
    api
      .llmStatus()
      .then((res) => setState({ ok: res?.data?.ready, model: res?.data?.model || '' }))
      .catch(() => setState({ ok: false, model: '' }))
  }, [])

  const text = state.ok === null ? '检测中…' : state.ok ? `DeepSeek 就绪 · ${state.model}` : '大模型未配置'
  const cls = state.ok ? 'badge ok' : state.ok === null ? 'badge' : 'badge bad'
  return <span className={cls}>{text}</span>
}

/* ─────────────── 经营看板 ─────────────── */
function Board() {
  const [overview, setOverview] = useState(null)
  const [sales, setSales] = useState([])
  const [low, setLow] = useState([])
  const [hot, setHot] = useState([])
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    Promise.all([api.overview(), api.salesTop(5, 90), api.lowStock(10), api.hotQuestions(6)])
      .then(([o, s, l, h]) => {
        setOverview(o?.data || null)
        setSales(s?.data || [])
        setLow(l?.data || [])
        setHot(h?.data || [])
      })
      .catch((e) => setErr(e?.response?.data?.detail || e.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  const o = overview || {}
  const cards = [
    { label: '有效订单', value: o.valid_orders ?? '-', unit: '单' },
    { label: '成交额 GMV', value: o.gmv != null ? `¥${o.gmv.toLocaleString()}` : '-', unit: '' },
    { label: '钱包余额', value: o.wallet_balance != null ? `¥${o.wallet_balance.toLocaleString()}` : '-', unit: '' },
    { label: '待发货', value: o.pending_ship ?? '-', unit: '单' },
    { label: '库存预警', value: o.low_stock_count ?? '-', unit: '项', danger: true },
  ]

  return (
    <section>
      <div className="row-between">
        <h2>经营看板</h2>
        <button className="btn ghost" onClick={load} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
      </div>
      {err && <p className="error">⚠️ {err}</p>}

      <div className="cards">
        {cards.map((c) => (
          <div className={c.danger ? 'card danger' : 'card'} key={c.label}>
            <span className="card-label">{c.label}</span>
            <strong className="card-value">
              {c.value}
              <small>{c.unit}</small>
            </strong>
          </div>
        ))}
      </div>

      <div className="grid-2">
        <div className="panel">
          <h3>近 90 天销量 Top5</h3>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>商品</th>
                <th>销量</th>
                <th>销售额</th>
              </tr>
            </thead>
            <tbody>
              {sales.length === 0 && (
                <tr>
                  <td colSpan="4" className="muted">暂无数据</td>
                </tr>
              )}
              {sales.map((s, i) => (
                <tr key={`${s.product_sku}-${i}`}>
                  <td>{i + 1}</td>
                  <td>
                    {s.product_name}
                    {s.product_sku && <span className="sku">{s.product_sku}</span>}
                  </td>
                  <td>{s.sold_qty} 件</td>
                  <td>¥{Number(s.revenue).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel">
          <h3>库存预警（可用 ≤ 预警线）</h3>
          <table>
            <thead>
              <tr>
                <th>商品</th>
                <th>可用</th>
                <th>预警线</th>
              </tr>
            </thead>
            <tbody>
              {low.length === 0 && (
                <tr>
                  <td colSpan="3" className="muted">暂无预警</td>
                </tr>
              )}
              {low.map((l, i) => (
                <tr key={`${l.product_sku}-${i}`}>
                  <td>
                    {l.product_name}
                    {l.product_sku && <span className="sku">{l.product_sku}</span>}
                  </td>
                  <td className="strong-danger">{l.available_stock}</td>
                  <td>{l.alert_line}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h3>客户近期咨询热点（来自 ZT-agent 客服对话）</h3>
        {hot.length === 0 ? (
          <p className="muted">暂无咨询记录</p>
        ) : (
          <ul className="hot-list">
            {hot.map((h, i) => (
              <li key={i}>
                <span className="hot-text">{h.content || '(空)'}</span>
                <span className="muted small">{h.created_at}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

/* ─────────────── 文案生成 ─────────────── */
function Generator({ onGenerated }) {
  const [form, setForm] = useState({
    brief: '推一下蓝鲫X5，冲一波秋季销量',
    channel: '朋友圈',
    tone: '促销',
    extra: '',
  })
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const update = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const submit = async () => {
    setLoading(true)
    setErr('')
    setResult(null)
    try {
      const res = await api.generate(form)
      setResult(res?.data || null)
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message || '生成失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section>
      <h2>文案生成</h2>
      <p className="hint">
        Agent 会<b>自主调用只读工具</b>查询销量排行与库存预警，再据此写文案 —— 数据全部来自 ZT-agent 的真实业务库。
      </p>

      <div className="panel">
        <div className="form-grid">
          <label className="full">
            运营需求
            <textarea rows="3" value={form.brief} onChange={update('brief')} placeholder="例如：推一下蓝鲫X5，冲一波秋季销量" />
          </label>
          <label>
            投放渠道
            <select value={form.channel} onChange={update('channel')}>
              {CHANNELS.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <label>
            文案语气
            <select value={form.tone} onChange={update('tone')}>
              {TONES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="full">
            补充要求（可选）
            <input value={form.extra} onChange={update('extra')} placeholder="例如：突出性价比 / 面向批发客户" />
          </label>
        </div>

        <div className="row-between mt">
          <button className="btn primary" onClick={submit} disabled={loading || !form.brief.trim()}>
            {loading ? 'Agent 生成中（会先查数据）…' : '生成营销文案'}
          </button>
          {result && (
            <button className="btn ghost" onClick={onGenerated}>
              去审核台 →
            </button>
          )}
        </div>
        {err && <p className="error">⚠️ {err}</p>}
      </div>

      {result && (
        <div className="panel">
          <div className="row-between">
            <h3>生成结果（已存为「待审核」草稿 #{result.id}）</h3>
            <span className={STATUS_CLS[result.status] || 'pill'}>{result.status}</span>
          </div>
          <pre className="draft-content">{result.content}</pre>
        </div>
      )}
    </section>
  )
}

/* ─────────────── 审核台 ─────────────── */
function ReviewDesk() {
  const [filter, setFilter] = useState('待审核')
  const [list, setList] = useState([])
  const [stats, setStats] = useState({})
  const [loading, setLoading] = useState(false)
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([api.drafts(filter), api.stats()])
      .then(([d, s]) => {
        setList(d?.data || [])
        setStats(s?.data || {})
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [filter])

  useEffect(load, [load])

  const doReview = async (id, status) => {
    await api.review(id, { status, reviewer: '运营-汤', review_note: note })
    setNote('')
    load()
  }

  const doDelete = async (id) => {
    if (!window.confirm('确认删除这条草稿？')) return
    await api.removeDraft(id)
    load()
  }

  return (
    <section>
      <div className="row-between">
        <h2>审核台</h2>
        <div className="filters">
          {['待审核', '已通过', '已驳回', ''].map((s) => (
            <button
              key={s || 'all'}
              className={filter === s ? 'chip active' : 'chip'}
              onClick={() => setFilter(s)}
            >
              {s || '全部'}
            </button>
          ))}
        </div>
      </div>

      <div className="cards small">
        <div className="card">
          <span className="card-label">草稿总数</span>
          <strong className="card-value">{stats.total ?? 0}</strong>
        </div>
        <div className="card">
          <span className="card-label">待审核</span>
          <strong className="card-value warn-text">{stats['待审核'] ?? 0}</strong>
        </div>
        <div className="card">
          <span className="card-label">已通过</span>
          <strong className="card-value ok-text">{stats['已通过'] ?? 0}</strong>
        </div>
        <div className="card">
          <span className="card-label">已驳回</span>
          <strong className="card-value bad-text">{stats['已驳回'] ?? 0}</strong>
        </div>
      </div>

      <div className="panel">
        <label className="full">
          审核意见（驳回时建议填写）
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="例如：价格表述需再确认 / 语气太硬" />
        </label>
      </div>

      {loading && <p className="muted">加载中…</p>}
      {!loading && list.length === 0 && <p className="muted">该状态下暂无草稿。</p>}

      {list.map((d) => (
        <div className="panel draft" key={d.id}>
          <div className="row-between">
            <h3>
              #{d.id} {d.title}
            </h3>
            <span className={STATUS_CLS[d.status] || 'pill'}>{d.status}</span>
          </div>
          <p className="meta">
            {d.channel} · {d.tone} · {d.created_at}
            {d.reviewer && ` · 审核人：${d.reviewer}`}
            {d.review_note && ` · 意见：${d.review_note}`}
          </p>
          <pre className="draft-content">{d.content}</pre>
          <div className="actions">
            <button className="btn ok" onClick={() => doReview(d.id, '已通过')} disabled={d.status === '已通过'}>
              通过
            </button>
            <button className="btn warn" onClick={() => doReview(d.id, '已驳回')} disabled={d.status === '已驳回'}>
              驳回
            </button>
            <button className="btn ghost" onClick={() => doDelete(d.id)}>
              删除
            </button>
          </div>
        </div>
      ))}
    </section>
  )
}
