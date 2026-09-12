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
  const [auto, setAuto] = useState({ runs: [] })
  const [filter, setFilter] = useState('待审批')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [showAudit, setShowAudit] = useState(false)
  const [showAuto, setShowAuto] = useState(false)
  const [showCoord, setShowCoord] = useState(false)
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
      api.autoPilot(),
    ])
      .then(([t, s, p, z, a, ap]) => {
        setTasks(t?.data?.items || [])
        setStats(s?.data || {})
        setPolicy(p?.data?.policy || {})
        setLabels(p?.data?.labels || {})
        setZt(z?.data || null)
        setActions(a?.data?.actions || [])
        setLevels(a?.data?.levels || [])
        setAuto(ap?.data || { runs: [] })
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

  /* 自动运营：一键开/关；开启后调度器按间隔自己巡检，人只看报告 */
  const toggleAutoPilot = () =>
    run('auto', async () => {
      const on = policy.autopilot_enabled !== 'on'
      const res = await api.updatePolicy({
        autopilot_enabled: on ? 'on' : 'off',
        actor: '运营-汤',
      })
      return {
        message: on
          ? `自动运营已开启：每 ${policy.autopilot_interval_minutes || 30} 分钟自动巡检一轮；能自动做什么由「数据库写入权限」与动作白名单决定（退货永远转人工）`
          : '自动运营已关闭，系统不再自行巡检',
        ...res,
      }
    })

  /* 立即跑一轮：不等定时器，马上验证策略是否生效 */
  const runAutoNow = () =>
    run('auto-run', async () => {
      const res = await api.autoPilotRun('manual')
      load()
      return { message: res?.data?.message || res?.message || '已执行一轮' }
    })

  const patchPayload = (task, key, value) =>
    run(`patch-${task.id}`, async () => {
      const res = await api.patchTask(task.id, { [key]: value })
      return { message: res?.message }
    })

  const isLive = policy.execute_mode === 'live'
  const killed = policy.kill_switch === 'on'
  const autoOn = policy.autopilot_enabled === 'on'
  const levelName = (levels.find((l) => l.code === policy.global_level) || {}).name || ''

  return (
    <section>
      <div className="row-between">
        <h2>运营任务中心</h2>
        <div className="filters">
          <button className="btn ghost" onClick={() => setShowAudit(!showAudit)}>
            {showAudit ? '收起操作记录' : '查看操作记录'}
          </button>
          <button className="btn ghost" onClick={() => setShowAuto(!showAuto)}>
            {showAuto ? '收起自动运营记录' : '🤖 自动运营记录'}
          </button>
          <button className="btn ghost" onClick={() => setShowCoord(!showCoord)}>
            {showCoord ? '收起跨 Agent 协作' : '🤝 跨 Agent 协作'}
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
          <span className="autobar-item">
            <b>自动运营</b>
            <span className={autoOn ? (killed ? 'pill warn' : 'pill ok') : 'pill'}>
              {autoOn ? (killed ? '已开启（被急停拦截）' : '运行中') : '未开启'}
            </span>
            {autoOn && auto.next_run_at && <span className="muted small">下一轮 {auto.next_run_at}</span>}
          </span>
          <span className="autobar-item">
            <b>跨Agent协作</b>
            <span className={policy.coord_enabled === 'on' ? 'pill ok' : 'pill'}>
              {policy.coord_enabled === 'on'
                ? `已开启${policy.coord_auto_apply === 'on' ? '（自动下发）' : '（转人工确认）'}`
                : '未开启'}
            </span>
            {stats.coord_pending_count > 0 && (
              <span className="pill warn">{stats.coord_pending_count} 条待确认</span>
            )}
          </span>
        </div>
        <div className="autobar-btns">
          <button className={autoOn ? 'btn ghost' : 'btn primary'} onClick={toggleAutoPilot} disabled={busy === 'auto'}>
            {busy === 'auto' ? '切换中…' : autoOn ? '⏹ 关闭自动运营' : '🤖 开启自动运营'}
          </button>
          <button className={killed ? 'btn ok' : 'btn danger'} onClick={toggleKill} disabled={busy === 'kill'}>
            {killed ? '▶ 恢复自动执行' : '⏸ 急停：暂停所有自动执行'}
          </button>
        </div>
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
          <p className="panel-sub">
            写入权限=只读时，所有写动作一律转人工；开放权限后，补货 / 发货 / 取消可在护栏（等级 + 限额 + 配额 + 白名单）内自动执行，
            发货运单号自动生成。退货涉及资金流出，任何配置下都永远人工审批。所有写入均经 ZT-agent 接口，不直接碰数据库。
          </p>
          <div className="settings-grid">
            <label>
              数据库写入权限
              <select
                value={policy.db_write_scope || 'readonly'}
                onChange={(e) => savePolicy({ db_write_scope: e.target.value })}
              >
                <option value="readonly">只读（所有写动作转人工）</option>
                <option value="inventory">开放库存写（补货可自动）</option>
                <option value="inventory+orders">开放库存+订单写（发货/取消可自动）</option>
              </select>
              <span className="tip">控制自动执行允许改哪类数据；权限不开放时，勾了白名单也不会自动执行。</span>
            </label>
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
            <label style={{ gridColumn: '1 / -1' }}>
              允许自动执行的动作（多选）
              <div className="chk-row">
                {(actions.length ? actions : [
                  { code: 'restock', name: '补充库存', icon: '📦', auto_eligible: true },
                  { code: 'ship', name: '订单发货', icon: '🚚', auto_eligible: false },
                  { code: 'return', name: '退货处理', icon: '↩️', auto_eligible: false },
                  { code: 'cancel', name: '取消订单', icon: '🚫', auto_eligible: false },
                ]).map((a) => {
                  const cur = (policy.auto_actions || '').split(',').map((s) => s.trim()).filter(Boolean)
                  const on = cur.includes(a.code)
                  return (
                    <label key={a.code} className={`chk${on ? ' on' : ''}`}>
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={(e) => {
                          const next = e.target.checked
                            ? Array.from(new Set([...cur, a.code]))
                            : cur.filter((c) => c !== a.code)
                          savePolicy({ auto_actions: next.join(',') })
                        }}
                      />
                      {a.icon} {a.name}
                      {!a.auto_eligible && <span className="small muted">（需开放对应写入权限）</span>}
                    </label>
                  )
                })}
              </div>
              <span className="tip">勾选 = 允许进入自动评估；实际能否自动执行还取决于写入权限、自主等级与限额配额。退货处理永远转人工。</span>
            </label>
            <label>
              默认快递公司（自动发货用）
              <input
                defaultValue={policy.default_ship_company || ''}
                onBlur={(e) => savePolicy({ default_ship_company: e.target.value })}
                placeholder="留空 = 中通快递"
              />
              <span className="tip">自动执行发货时填入；运单号按「AUTO+时间戳」自动生成。</span>
            </label>
            <label>
              单次自动补货上限（件）
              <NumSelect
                value={policy.auto_restock_max_qty}
                options={['5', '10', '20', '50', '100']}
                onChange={(v) => savePolicy({ auto_restock_max_qty: v })}
              />
              <span className="tip">L2 限额内自动时生效；超过就转人工。</span>
            </label>
            <label>
              单次自动补货上限（元）
              <NumSelect
                value={policy.auto_restock_max_amount}
                options={['100', '300', '500', '1000', '2000']}
                onChange={(v) => savePolicy({ auto_restock_max_amount: v })}
              />
              <span className="tip">按进货成本估算，控制一次自动花的钱。</span>
            </label>
            <label>
              每日自动执行配额（次）
              <NumSelect
                value={policy.daily_auto_quota}
                options={['5', '10', '20', '50', '100']}
                onChange={(v) => savePolicy({ daily_auto_quota: v })}
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
            <label>
              自动运营
              <select
                value={policy.autopilot_enabled || 'off'}
                onChange={(e) => savePolicy({ autopilot_enabled: e.target.value })}
              >
                <option value="off">关闭（只在你点巡检时才工作）</option>
                <option value="on">开启（按间隔自己巡检、自己干活）</option>
              </select>
              <span className="tip">开启后无需人工触发；能自动做什么仍由上面几项策略决定。</span>
            </label>
            <label>
              自动巡检间隔（分钟）
              <NumSelect
                value={policy.autopilot_interval_minutes}
                options={['15', '30', '60', '120', '240']}
                onChange={(v) => savePolicy({ autopilot_interval_minutes: v })}
              />
              <span className="tip">建议 30~120 分钟；太频繁没有意义，数据变化没那么快。</span>
            </label>
            <label>
              跨 Agent 协作总开关
              <select
                value={policy.coord_enabled || 'off'}
                onChange={(e) => savePolicy({ coord_enabled: e.target.value })}
              >
                <option value="off">关闭（两个 Agent 各干各的）</option>
                <option value="on">开启（与前台客服 Agent 联动）</option>
              </select>
              <span className="tip">开启后：断货自动通知前台挂提示，退货飙升生成复盘任务。</span>
            </label>
            <label>
              协调指令自动下发
              <select
                value={policy.coord_auto_apply || 'off'}
                onChange={(e) => savePolicy({ coord_auto_apply: e.target.value })}
              >
                <option value="off">转人工（只建议，我来点确认）</option>
                <option value="on">自动（发现即下发到前台生效）</option>
              </select>
              <span className="tip">建议先用「转人工」观察一段时间，再放开自动。</span>
            </label>
            <label>
              退货率告警阈值（%）
              <NumSelect
                value={policy.coord_return_surge_threshold}
                options={['5', '10', '20', '30', '50']}
                onChange={(v) => savePolicy({ coord_return_surge_threshold: v })}
              />
              <span className="tip">某商品退货率超过该值即标记「需复盘」；样本不足 3 单不触发。</span>
            </label>
          </div>
        </div>
      )}

      {/* ── 自动运营记录 ── */}
      {showAuto && <AutoPanel auto={auto} onRunNow={runAutoNow} busy={busy} />}

      {/* ── 跨 Agent 协作 ── */}
      {showCoord && <CoordPanel busy={busy} run={run} enabled={policy.coord_enabled === 'on'} />}

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

/* ─────────────── 自动运营记录 ─────────────── */
const TRIGGER_LABEL = { timer: '定时自动', manual: '页面手动', api: '接口触发' }

function AutoPanel({ auto, onRunNow, busy }) {
  const runs = auto?.runs || []
  return (
    <div className="panel">
      <div className="row-between">
        <h3>
          自动运营记录
          <span className={auto?.enabled ? 'pill ok' : 'pill'} style={{ marginLeft: 8 }}>
            {auto?.enabled ? '运行中' : '未开启'}
          </span>
        </h3>
        <button className="btn ghost" onClick={onRunNow} disabled={busy === 'auto-run'}>
          {busy === 'auto-run' ? '执行中…' : '▶ 立即跑一轮'}
        </button>
      </div>
      <p className="panel-sub">
        这就是「后期自己运营」的效果：系统按你设定的间隔，自动巡检一次 →
        生成待办 → 该自动的按护栏自动执行 → 剩下的等人审批。每一轮都留痕，可随时复盘它到底干了什么。
      </p>
      <p className="meta">
        下一轮时间：<b>{auto?.enabled ? auto?.next_run_at || '即将执行' : '—（未开启）'}</b>
        {auto?.enabled && ` · 间隔：每 ${auto?.interval_minutes || '-'} 分钟`}
        {' · '}急停：
        <b>{auto?.kill_switch === 'on' ? '已开启（只巡检不自动执行）' : '未开启'}</b>
      </p>

      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>触发方式</th>
            <th>巡检结果</th>
            <th>新建待办</th>
            <th>自动执行</th>
            <th>转人工</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td className="nowrap">{r.started_at}</td>
              <td>{TRIGGER_LABEL[r.trigger] || r.trigger}</td>
              <td className="muted">
                缺货 {r.scanned?.restock_candidates ?? 0} · 待发货 {r.scanned?.ship_candidates ?? 0} ·
                售后线索 {r.scanned?.after_sale_hints ?? 0}
              </td>
              <td>{r.created_count}</td>
              <td>{r.auto_count > 0 ? <span className="pill ok">{r.auto_count}</span> : 0}</td>
              <td>{r.pending_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {runs.length === 0 && (
        <p className="muted">
          还没有执行记录。点「▶ 立即跑一轮」试一次，就会在这里看到。
        </p>
      )}
    </div>
  )
}

/* ─────────────── 跨 Agent 协作面板（L5 编排） ─────────────── */
const KIND_LABEL = {
  stockout_pause: '🚫 断货暂停购买',
  return_surge: '📉 退货率飙升',
  inbound_return: '↩️ ZT退货告知',
}
const DIR_LABEL = {
  'marketing→zt': '营销 → 前台',
  'zt→marketing': '前台 → 营销',
  internal: '营销内部',
}
const COORD_CLS = {
  applied: 'pill ok',
  pending: 'pill warn',
  received: 'pill info',
  resolved: 'pill',
  skipped: 'pill',
}

function CoordPanel({ busy, run, enabled }) {
  const [events, setEvents] = useState([])
  const [kinds, setKinds] = useState({})

  const loadEvents = useCallback(() => {
    api.coordEvents(50)
      .then((r) => {
        setEvents(r?.data?.items || [])
        setKinds(r?.data?.kinds || {})
      })
      .catch(() => {})
  }, [])
  useEffect(loadEvents, [loadEvents])

  const doCoordScan = () =>
    run('coord-scan', async () => {
      const res = await api.coordScan()
      const d = res?.data || {}
      loadEvents()
      return {
        message:
          `协调巡检完成：自动下发 ${d.stockout_emitted?.length || 0} 条，` +
          `转人工 ${d.stockout_pending?.length || 0} 条，` +
          `退货复盘 ${d.return_surge_flagged?.length || 0} 条`,
      }
    })

  const doApply = (id) =>
    run(`coord-${id}`, async () => {
      const res = await api.coordApply(id)
      loadEvents()
      return { message: res?.message || '已处理' }
    })

  return (
    <div className="panel">
      <div className="row-between">
        <h3>
          跨 Agent 协作（L5 多智能体编排）
          <span className={enabled ? 'pill ok' : 'pill'} style={{ marginLeft: 8 }}>
            {enabled ? '已开启' : '未开启'}
          </span>
        </h3>
        <button className="btn ghost" onClick={doCoordScan} disabled={busy === 'coord-scan'}>
          {busy === 'coord-scan' ? '巡检中…' : '🔄 立即协调巡检'}
        </button>
      </div>
      <p className="panel-sub">
        这是两个 AI 助手「互相打招呼」的地方：<b>营销助手</b>发现商品断货 →
        通知<b>前台客服助手</b>暂停购买、挂出缺货提示，避免客户下单买不到的东西；
        前台收到退货申请 → 通知营销助手复盘该商品的售后策略。
        所有往来都留痕在下面，可以在「自主化设置」里控制是否自动下发。
      </p>

      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>方向</th>
            <th>类型</th>
            <th>商品</th>
            <th>说明</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td className="nowrap">{e.created_at}</td>
              <td className="nowrap">{DIR_LABEL[e.direction] || e.direction}</td>
              <td className="nowrap">{KIND_LABEL[e.kind] || kinds[e.kind] || e.kind}</td>
              <td className="nowrap">{e.product_name || `#${e.product_id}`}</td>
              <td className="muted">{e.detail}</td>
              <td>
                <span className={COORD_CLS[e.status] || 'pill'}>
                  {e.status === 'applied' && '已下发生效'}
                  {e.status === 'pending' && '待确认'}
                  {e.status === 'received' && '已登记'}
                  {e.status === 'resolved' && '已处理'}
                  {e.status === 'skipped' && '已跳过'}
                </span>
              </td>
              <td>
                {e.status === 'pending' && (
                  <button className="btn primary sm" onClick={() => doApply(e.id)} disabled={busy === `coord-${e.id}`}>
                    {busy === `coord-${e.id}` ? '处理中…' : '✓ 确认下发'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.length === 0 && (
        <p className="muted">
          暂无协调事件。开启「跨 Agent 协作总开关」后点「🔄 立即协调巡检」，
          系统会检查断货与退货情况并生成协调记录。
        </p>
      )}
    </div>
  )
}

/* 数字类策略的下拉选择：当前值不在候选里时自动补进选项，避免显示错位 */
function NumSelect({ value, options, onChange }) {
  const v = String(value ?? '')
  const opts = v && !options.includes(v) ? [v, ...options] : options
  return (
    <select value={v} onChange={(e) => onChange(e.target.value)}>
      {opts.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  )
}
