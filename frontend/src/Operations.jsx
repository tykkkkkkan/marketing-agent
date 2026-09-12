import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

/* 状态 → 视觉 */
const TASK_CLS = {
  待审批: 'pill warn',
  已批准: 'pill info',
  已执行: 'pill ok',
  执行失败: 'pill bad',
  已驳回: 'pill',
  已撤销: 'pill',
}
const RISK_CLS = { 低: 'pill ok', 中: 'pill warn', 高: 'pill bad' }

const FILTERS = [
  ['待审批', '等我审批'],
  ['已执行', '已执行'],
  ['执行失败', '执行失败'],
  ['已驳回', '已驳回'],
  ['', '全部'],
]

/**
 * 运营任务中心 —— 让企业人员看懂「AI 想做什么 / 为什么 / 我该点哪个」
 * 三块：自主化状态条（含急停）· 待办清单（含护栏解释）· 自主化设置
 */
export default function Operations() {
  const [tasks, setTasks] = useState([])
  const [stats, setStats] = useState({})
  const [policy, setPolicy] = useState({})
  const [labels, setLabels] = useState({})
  const [zt, setZt] = useState(null)
  const [actions, setActions] = useState([])
  const [levels, setLevels] = useState([])
  const [audit, setAudit] = useState([])
  const [filter, setFilter] = useState('待审批')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [showAudit, setShowAudit] = useState(false)
  const [expanded, setExpanded] = useState(null)
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      api.tasks(filter),
      api.opsStats(),
      api.policy(),
      api.ztStatus(),
      api.actions(),
    ])
      .then(([t, s, p, z, a]) => {
        setTasks(t?.data?.items || [])
        setStats(s?.data || {})
        setPolicy(p?.data?.policy || {})
        setLabels(p?.data?.labels || {})
        setZt(z?.data || null)
        setActions(a?.data?.actions || [])
        setLevels(a?.data?.levels || [])
      })
      .catch((e) => setErr(e?.response?.data?.detail || e.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [filter])

  useEffect(load, [load])

  const run = async (label, fn) => {
    setBusy(label)
    setErr('')
    setMsg('')
    try {
      const res = await fn()
      setMsg(res?.message || '操作完成')
      load()
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message || '操作失败')
    } finally {
      setBusy('')
    }
  }

  const doScan = () =>
    run('scan', async () => {
      const res = await api.scan()
      const created = res?.data?.created_task_ids?.length || 0
      const auto = res?.data?.auto_executed_task_ids?.length || 0
      const hints = res?.data?.scanned?.after_sale_hints || 0
      setFilter('待审批')
      return { message: `巡检完成：新增 ${created} 条待办，自动执行 ${auto} 条，售后线索 ${hints} 条` }
    })

  const toggleKill = () =>
    run('kill', async () => {
      const on = policy.kill_switch !== 'on'
      const res = await api.killSwitch(on)
      return { message: res?.message }
    })

  const savePolicy = (patch) => run('policy', () => api.updatePolicy({ ...patch, actor: '运营-汤' }))

  const patchPayload = (task, key, value) =>
    run(`patch-${task.id}`, async () => {
      const res = await api.patchTask(task.id, { [key]: value })
      return { message: res?.message }
    })

  const isLive = policy.execute_mode === 'live'
  const killed = policy.kill_switch === 'on'
  const levelName = (levels.find((l) => l.code === policy.global_level) || {}).name || ''

  return (
    <section>
      <div className="row-between">
        <h2>运营任务中心</h2>
        <div className="filters">
          <button className="btn ghost" onClick={() => setShowAudit(!showAudit)}>
            {showAudit ? '收起操作记录' : '查看操作记录'}
          </button>
          <button className="btn ghost" onClick={() => setShowSettings(!showSettings)}>
            {showSettings ? '收起自主化设置' : '⚙️ 自主化设置'}
          </button>
        </div>
      </div>
      <p className="hint">
        这里是「AI 帮你办事」的地方：它先看数据、提出建议，<b>你点头它才动手</b>。
        每条待办都写清楚了「为什么建议做」和「AI 为什么没有自己做」，你可以放心逐条把关。
      </p>

      {err && <p className="error">⚠️ {err}</p>}
      {msg && <p className="okmsg">✅ {msg}</p>}

      {/* ── 自主化状态条 ── */}
      <div className={killed ? 'autobar killed' : 'autobar'}>
        <div className="autobar-left">
          <span className="autobar-item">
            <b>自主等级</b>
            <span className="pill info">{policy.global_level} {levelName}</span>
          </span>
          <span className="autobar-item">
            <b>执行模式</b>
            <span className={isLive ? 'pill ok' : 'pill warn'}>
              {isLive ? '真实执行（会改数据）' : '演练模式（不落地）'}
            </span>
          </span>
          <span className="autobar-item">
            <b>收银系统</b>
            <span className={zt?.reachable && zt?.login_ok ? 'pill ok' : 'pill bad'}>
              {zt?.reachable ? (zt?.login_ok ? '已连通' : '连不上（凭据未配置）') : '未连接'}
            </span>
          </span>
          <span className="autobar-item">
            <b>今日自动执行</b>
            <span className="pill">
              {stats.today_auto_count ?? 0} / {stats.daily_auto_quota ?? '-'} 次
            </span>
          </span>
        </div>
        <button className={killed ? 'btn ok' : 'btn danger'} onClick={toggleKill} disabled={busy === 'kill'}>
          {killed ? '▶ 恢复自动执行' : '⏸ 急停：暂停所有自动执行'}
        </button>
      </div>
      {zt && !zt.login_ok && zt.reachable && (
        <p className="muted small">
          提示：{zt.message}——配置好账号密码后即可真实执行；在此之前建议保持「演练模式」。
        </p>
      )}

      {/* ── 自主化设置 ── */}
      {showSettings && (
        <div className="panel">
          <h3>自主化设置（决定 AI 什么时候可以自己动手）</h3>
          <p className="panel-sub">哪怕调到最宽松，只要动作「不可逆」或「涉及资金」（发货、退货、取消），也必须人工审批。</p>
          <div className="settings-grid">
            <label>
              自主等级
              <select
                value={policy.global_level || 'L1'}
                onChange={(e) => savePolicy({ global_level: e.target.value })}
              >
                {levels.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.code} {l.name}
                  </option>
                ))}
              </select>
              <span className="tip">{levels.find((l) => l.code === policy.global_level)?.desc}</span>
            </label>
            <label>
              执行模式
              <select
                value={policy.execute_mode || 'live'}
                onChange={(e) => savePolicy({ execute_mode: e.target.value })}
              >
                <option value="dry_run">演练模式（只演示，不改数据）</option>
                <option value="live">真实执行（真的改库存/订单）</option>
              </select>
              <span className="tip">建议先演练确认流程，再切到真实执行。</span>
            </label>
            <label>
              允许自动执行的动作
              <input
                defaultValue={policy.auto_actions || ''}
                onBlur={(e) => savePolicy({ auto_actions: e.target.value })}
                placeholder="restock, ship ..."
              />
              <span className="tip">多个用英文逗号分隔；不在名单里的动作一律转人工。</span>
            </label>
            <label>
              单次自动补货上限（件）
              <input
                defaultValue={policy.auto_restock_max_qty || ''}
                onBlur={(e) => savePolicy({ auto_restock_max_qty: e.target.value })}
              />
              <span className="tip">L2 限额内自动时生效；超过就转人工。</span>
            </label>
            <label>
              单次自动补货上限（元）
              <input
                defaultValue={policy.auto_restock_max_amount || ''}
                onBlur={(e) => savePolicy({ auto_restock_max_amount: e.target.value })}
              />
              <span className="tip">按进货成本估算，控制一次自动花的钱。</span>
            </label>
            <label>
              每日自动执行配额（次）
              <input
                defaultValue={policy.daily_auto_quota || ''}
                onBlur={(e) => savePolicy({ daily_auto_quota: e.target.value })}
              />
              <span className="tip">一天的自动执行上限，到顶后全部转人工。</span>
            </label>
            <label>
              目标白名单（可选）
              <input
                defaultValue={policy.auto_target_whitelist || ''}
                onBlur={(e) => savePolicy({ auto_target_whitelist: e.target.value })}
                placeholder="商品ID或订单号，逗号分隔；留空=不限制"
              />
              <span className="tip">只允许对这些目标自动执行，适合先小范围试点。</span>
            </label>
          </div>
        </div>
      )}

      {/* ── 操作记录 ── */}
      {showAudit && <AuditPanel />}

      {/* ── 待办清单 ── */}
      <div className="row-between mt">
        <div className="filters">
          {FILTERS.map(([k, label]) => (
            <button key={k || 'all'} className={filter === k ? 'chip active' : 'chip'} onClick={() => setFilter(k)}>
              {label}
              {k === '待审批' && stats.by_status?.['待审批'] ? ` (${stats.by_status['待审批']})` : ''}
            </button>
          ))}
        </div>
        <button className="btn primary" onClick={doScan} disabled={busy === 'scan'}>
          {busy === 'scan' ? '正在巡检…' : '🔍 智能巡检，生成待办'}
        </button>
      </div>

      <div className="panel">
        <label className="full">
          审批意见（驳回时建议填写）
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="例如：先小批量补货试试 / 运单号待确认" />
        </label>
      </div>

      {loading && <p className="muted">加载中…</p>}
      {!loading && tasks.length === 0 && (
        <p className="muted">该状态下暂无任务。点右上角「智能巡检，生成待办」让 AI 先去看一眼数据。</p>
      )}

      {tasks.map((t) => (
        <TaskCard
          key={t.id}
          task={t}
          note={note}
          busy={busy}
          expanded={expanded === t.id}
          onToggle={() => setExpanded(expanded === t.id ? null : t.id)}
          onApprove={() => run(`a-${t.id}`, () => api.approveTask(t.id, note))}
          onReject={() => run(`r-${t.id}`, () => api.rejectTask(t.id, note))}
          onCancel={() => run(`c-${t.id}`, () => api.cancelTask(t.id, note))}
          onPatch={(k, v) => patchPayload(t, k, v)}
        />
      ))}
    </section>
  )
}

/* ─────────────── 单条任务卡 ─────────────── */
function TaskCard({ task: t, note, busy, expanded, onToggle, onApprove, onReject, onCancel, onPatch }) {
  const pending = t.status === '待审批'
  // 待审批但缺少必填参数（典型：发货没填运单号）→ 就地补录
  const missing = pending
    ? Object.entries(t.payload || {}).filter(([k, v]) => (k === 'ship_company' || k === 'tracking_no') && !String(v || '').trim())
    : []
  const [draft, setDraft] = useState({})

  return (
    <div className={`panel task risk-${t.risk}`}>
      <div className="row-between">
        <h3>
          {t.icon} {t.title}
          <span className="task-id">#{t.id}</span>
        </h3>
        <span className={TASK_CLS[t.status] || 'pill'}>{t.status}</span>
      </div>

      <p className="meta">
        {t.target_label && <>对象：{t.target_label} · </>}
        {t.quantity ? `数量：${t.quantity} · ` : ''}
        {t.amount ? `预估金额：¥${t.amount} · ` : ''}
        风险：<span className={RISK_CLS[t.risk_label] || 'pill'}>{t.risk_label}</span> ·
        来源：{t.source === 'scan' ? '系统巡检' : '人工发起'} · {t.created_at}
        {t.auto_executed && ' · 🤖 系统自动执行'}
      </p>

      {/* 为什么建议做 */}
      {t.reason && (
        <div className="why">
          <span className="why-label">为什么建议做</span>
          <span>{t.reason}</span>
        </div>
      )}

      {/* 护栏判定 */}
      <button className="linkbtn" onClick={onToggle}>
        {expanded ? '▾' : '▸'} AI 为什么{ t.status === '待审批' ? '没有自己动手' : '这样处理'}？（{t.decision}）
      </button>
      {expanded && (
        <ul className="checks">
          {(t.decision_checks || []).map((c, i) => (
            <li key={i} className={c.passed ? 'pass' : 'stop'}>
              <span className="check-icon">{c.passed ? '✅' : '⛔'}</span>
              <b>{c.name}</b>
              <span>{c.detail}</span>
            </li>
          ))}
        </ul>
      )}

      {/* 执行回执 */}
      {(t.exec_message || t.executed_at) && (
        <div className={t.exec_ok === false ? 'receipt bad' : 'receipt'}>
          <span className="receipt-label">
            {t.exec_mode === 'dry_run' ? '演练回执' : '执行回执'}
            {t.executed_at ? ` · ${t.executed_at}` : ''}
          </span>
          <span>{t.exec_message}</span>
          {t.exec_mode === 'dry_run' && <span className="muted small">（当前为演练模式，未真正改动数据）</span>}
        </div>
      )}

      {/* 缺少必填参数 → 就地补录 */}
      {missing.length > 0 && (
        <div className="fillrow">
          <span className="fillrow-label">执行前还需填写：</span>
          {missing.map(([k]) => (
            <span key={k} className="fillrow-item">
              <input
                placeholder={k === 'ship_company' ? '快递公司，如 顺丰' : '运单号'}
                value={draft[k] || ''}
                onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
              />
              <button className="btn ghost sm" disabled={busy === `patch-${t.id}`} onClick={() => onPatch(k, draft[k])}>
                保存
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="actions">
        {pending && (
          <button className="btn primary" onClick={onApprove} disabled={busy === `a-${t.id}`}>
            {busy === `a-${t.id}` ? '执行中…' : '✓ 批准并执行'}
          </button>
        )}
        {pending && (
          <button className="btn warn" onClick={onReject} disabled={busy === `r-${t.id}`}>
            驳回
          </button>
        )}
        {t.status !== '已执行' && t.status !== '已撤销' && (
          <button className="btn ghost" onClick={onCancel} disabled={busy === `c-${t.id}`}>
            撤销
          </button>
        )}
      </div>
    </div>
  )
}

/* ─────────────── 操作记录 ─────────────── */
function AuditPanel() {
  const [rows, setRows] = useState([])
  useEffect(() => {
    api.opsAudit(30).then((r) => setRows(r?.data || [])).catch(() => {})
  }, [])
  const EVENT_LABEL = {
    created: '创建任务',
    approved: '人工批准',
    rejected: '人工驳回',
    cancelled: '撤销',
    executed: '执行成功',
    failed: '执行失败',
    policy_changed: '修改策略',
    updated: '补充参数',
  }
  return (
    <div className="panel">
      <h3>操作记录（谁、什么时候、做了什么）</h3>
      <p className="panel-sub">AI 自动执行与人工作业都会留痕，可随时追溯。</p>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>动作</th>
            <th>操作人</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id}>
              <td className="nowrap">{a.created_at}</td>
              <td>{EVENT_LABEL[a.event] || a.event}{a.task_id ? ` #${a.task_id}` : ''}</td>
              <td>{a.actor}</td>
              <td className="muted">{a.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <p className="muted">暂无记录</p>}
    </div>
  )
}
