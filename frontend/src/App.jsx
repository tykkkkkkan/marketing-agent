import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Operations from './Operations'

const CHANNELS = ['朋友圈', '社群', '公众号', '短视频口播', '短信', '直播']
const TONES = ['促销', '专业', '温情', '幽默', '种草']
const STATUS_CLS = { 待审核: 'pill warn', 已通过: 'pill ok', 已驳回: 'pill bad' }
const STATUS_LABEL = { 待审核: '待发布', 已通过: '已通过', 已驳回: '已驳回' }

export default function App() {
  const [tab, setTab] = useState('board')
  const [seedBrief, setSeedBrief] = useState('')

  // 从经营诊断一键跳到写文案，并带入诊断上下文
  const actOnDiagnosis = (brief) => {
    setSeedBrief(brief)
    setTab('generate')
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">🐟 中渔小助</span>
          <span className="sub">营销参谋 · 让经营数据帮你做决策</span>
        </div>
        <HealthBadge />
      </header>

      <nav className="tabs">
        {[
          ['board', '经营看板'],
          ['ops', '运营任务'],
          ['generate', '写营销文案'],
          ['review', '审核台'],
        ].map(([key, label]) => (
          <button key={key} className={tab === key ? 'tab active' : 'tab'} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>

      <main className="content">
        {tab === 'board' && <Board onActOnDiagnosis={actOnDiagnosis} />}
        {tab === 'ops' && <Operations />}
        {tab === 'generate' && <Generator initialBrief={seedBrief} onGenerated={() => setTab('review')} />}
        {tab === 'review' && <ReviewDesk />}
      </main>

      <footer className="footer">
        数据来源：中渔天下收银系统（ZT-agent）实时同步 · 查看数据不写入业务库；确需变更时，
        由系统以「操作员」身份调用收银系统自身接口完成，全程留痕可追溯
      </footer>
    </div>
  )
}

/* ─────────────── 后端状态 ─────────────── */
function HealthBadge() {
  const [state, setState] = useState({ ok: null, model: '' })
  useEffect(() => {
    api.llmStatus()
      .then((res) => setState({ ok: res?.data?.ready, model: res?.data?.model || '' }))
      .catch(() => setState({ ok: false, model: '' }))
  }, [])
  const text = state.ok === null ? '检测中…' : state.ok ? `AI 已就绪 · ${state.model}` : 'AI 未配置'
  const cls = state.ok ? 'badge ok' : state.ok === null ? 'badge' : 'badge bad'
  return <span className={cls}>{text}</span>
}

/* ─────────────── 经营看板 ─────────────── */
function Board({ onActOnDiagnosis }) {
  const [diag, setDiag] = useState(null)
  const [sales, setSales] = useState([])
  const [low, setLow] = useState([])
  const [hot, setHot] = useState([])
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    Promise.all([
      api.diagnosis(),
      api.salesTop(5, 90),
      api.lowStock(10),
      api.hotQuestions(8),
    ])
      .then(([d, s, l, h]) => {
        setDiag(d?.data || null)
        setSales(s?.data || [])
        setLow(l?.data || [])
        setHot(h?.data || [])
      })
      .catch((e) => setErr(e?.response?.data?.detail || e.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  const m = diag?.metrics || {}

  // 指标卡：业务语言 + 一句人话释义
  const metricCards = [
    { label: '近90天营业额', value: m.gmv != null ? `¥${Number(m.gmv).toLocaleString()}` : '-', tip: '所有成交订单的金额合计（GMV）' },
    { label: '成交订单', value: m.valid_orders ?? '-', unit: '单', tip: '已付款、未取消的订单数量' },
    { label: '客单价', value: m.avg_order_value != null ? `¥${m.avg_order_value}` : '-', tip: '平均每单的消费金额，反映顾客购买力' },
    { label: '回头客比例', value: m.repeat_rate != null ? `${m.repeat_rate}%` : '-', tip: '买过 2 次及以上的老客户占比' },
    { label: '库存健康度', value: m.healthy_ratio != null ? `${m.healthy_ratio}%` : '-', danger: (m.healthy_ratio ?? 100) < 70, tip: '库存充足的商品占比，预警商品越多越低' },
    { label: '退款退货率', value: m.refund_rate != null ? `${m.refund_rate}%` : '-', ok: (m.refund_rate ?? 100) < 5, tip: '退款/退货订单占全部订单的比例' },
  ]

  const maxQty = Math.max(1, ...sales.map((s) => s.sold_qty || 0))

  return (
    <section>
      <div className="row-between">
        <h2>经营看板</h2>
        <button className="btn ghost" onClick={load} disabled={loading}>
          {loading ? '加载中…' : '刷新数据'}
        </button>
      </div>
      {err && <p className="error">⚠️ {err}</p>}
      {loading && <div className="skeleton-block" />}

      {!loading && (
        <>
          {/* AI 经营诊断 */}
          {diag && (
            <div className="insight">
              <div className="insight-head">
                <span className="insight-title">🤖 AI 经营诊断</span>
                <span className="insight-time">数据快照 · {diag.generated_at}</span>
              </div>
              {diag.insight ? (
                <ul className="insight-list">
                  {diag.insight.split('\n').filter(Boolean).map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">AI 建议生成暂不可用（不影响下方真实数据）。</p>
              )}
              <div className="insight-foot">
                <button className="btn primary sm" onClick={() => onActOnDiagnosis(`根据以下经营诊断，帮我写一套营销方案：\n${diag.insight || ''}`)}>
                  据此生成营销文案 →
                </button>
                <span className="insight-foot-tip">带着诊断结论去写，文案更对路</span>
              </div>
            </div>
          )}

          {/* 核心指标 */}
          <div className="cards">
            {metricCards.map((c) => (
              <div className={c.danger ? 'card danger' : c.ok ? 'card ok' : 'card'} key={c.label}>
                <span className="card-label">{c.label}</span>
                <strong className="card-value">
                  {c.value}
                  {c.unit && <small>{c.unit}</small>}
                </strong>
                <span className="card-tip">{c.tip}</span>
              </div>
            ))}
          </div>

          <div className="grid-2">
            {/* 销量排行（条形图） */}
            <div className="panel">
              <h3>热销商品排行（近 90 天）</h3>
              {sales.length === 0 ? (
                <p className="muted">暂无销售数据</p>
              ) : (
                <div className="bars">
                  {sales.map((s, i) => (
                    <div className="bar-row" key={`${s.product_name}-${i}`}>
                      <span className="bar-name">{s.product_name}</span>
                      <div className="bar-track">
                        <div className="bar-fill" style={{ width: `${(s.sold_qty / maxQty) * 100}%` }} />
                      </div>
                      <span className="bar-val">{s.sold_qty} 件</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 库存预警 */}
            <div className="panel">
              <h3>库存预警（需要关注）</h3>
              {low.length === 0 ? (
                <p className="muted">当前库存充足 🎉</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>商品</th>
                      <th>可用 / 预警线</th>
                      <th>建议</th>
                    </tr>
                  </thead>
                  <tbody>
                    {low.map((l, i) => (
                      <tr key={`${l.product_name}-${i}`}>
                        <td>{l.product_name}</td>
                        <td className="strong-danger">
                          {l.available_stock} / {l.alert_line}
                        </td>
                        <td>
                          <span className="suggest">{l.available_stock <= 0 ? '尽快补货' : '谨慎促销'}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* 客户关心的问题 */}
          <div className="panel">
            <h3>客户最近在关心什么</h3>
            <p className="panel-sub">来自客服对话，帮你找准营销切入点</p>
            {hot.length === 0 ? (
              <p className="muted">暂无咨询记录</p>
            ) : (
              <div className="chips">
                {hot.map((h, i) => (
                  <span className="qchip" key={i}>
                    {h.content?.trim() || '(空)'}
                  </span>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  )
}

/* ─────────────── 文案生成 ─────────────── */
function Generator({ initialBrief, onGenerated }) {
  const [form, setForm] = useState({
    brief: initialBrief || '推一下蓝鲫X5，冲一波秋季野钓销量',
    channel: '朋友圈',
    tone: '促销',
    extra: '',
  })

  // 从经营诊断带入的上下文：切换时同步到表单
  useEffect(() => {
    if (initialBrief) setForm((f) => ({ ...f, brief: initialBrief }))
  }, [initialBrief])
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

  const ev = result?.evidence || {}

  return (
    <section>
      <h2>写营销文案</h2>
      <p className="hint">
        说一句大白话需求，AI 会先<b>查真实经营数据</b>（热销品、库存、客户关心的点），再写文案 —— 数据都来自你的收银系统，绝不瞎编。
      </p>

      <div className="panel">
        <div className="form-grid">
          <label className="full">
            营销需求（大白话即可）
            <textarea
              rows="3"
              value={form.brief}
              onChange={update('brief')}
              placeholder="例如：推一下蓝鲫X5，冲一波秋季野钓销量"
            />
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
            文案风格
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
            {loading ? 'AI 正在查数据、写文案…' : '生成营销文案'}
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
            <h3>生成结果（已存为「待发布」草稿 #{result.id}）</h3>
            <span className={STATUS_CLS[result.status] || 'pill'}>{STATUS_LABEL[result.status] || result.status}</span>
          </div>

          {/* 数据依据 */}
          {ev.summary && (
            <div className="evidence">
              <div className="evidence-head">📌 这篇文案基于的真实数据</div>
              <div className="evidence-summary">{ev.summary}</div>
              <div className="evidence-grid">
                <div>
                  <span className="ev-label">热销品</span>
                  <ul>
                    {(ev.sales_ranking || []).slice(0, 3).map((s, i) => (
                      <li key={i}>{s.product_name} · {s.sold_qty}件</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <span className="ev-label">库存预警</span>
                  <ul>
                    {(ev.low_stock || []).slice(0, 3).map((l, i) => (
                      <li key={i} className="ev-warn">{l.product_name} · 仅{l.available_stock}件</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <span className="ev-label">客户关心</span>
                  <ul>
                    {(ev.hot_questions || []).slice(0, 4).map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}

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
            <button key={s || 'all'} className={filter === s ? 'chip active' : 'chip'} onClick={() => setFilter(s)}>
              {s ? STATUS_LABEL[s] : '全部'}
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
          <span className="card-label">待发布</span>
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
            <span className={STATUS_CLS[d.status] || 'pill'}>{STATUS_LABEL[d.status] || d.status}</span>
          </div>
          <p className="meta">
            {d.channel} · {d.tone} · {d.created_at}
            {d.reviewer && ` · 审核人：${d.reviewer}`}
            {d.review_note && ` · 意见：${d.review_note}`}
          </p>

          {d.evidence?.summary && (
            <div className="evidence mini">
              <span className="ev-label">数据依据：</span>
              {d.evidence.summary}
            </div>
          )}

          <pre className="draft-content">{d.content}</pre>
          <div className="actions">
            <button className="btn ok" onClick={() => doReview(d.id, '已通过')} disabled={d.status === '已通过'}>
              通过发布
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
