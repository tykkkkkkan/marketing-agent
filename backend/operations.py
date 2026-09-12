"""
运营执行层（Action Layer）—— 让 Agent 从「只会给建议」走到「能真办事」
════════════════════════════════════════════════════════════════════
一句话：把「AI 的一句话建议」变成「一张可审批、可执行、可追溯的工单」。

四层结构
────────
  1. 动作注册表 ActionRegistry —— 系统能做哪些事、风险多大、参数是什么；
  2. 护栏引擎 Guardrails   —— 这一次到底该「自动执行」还是「等人点头」；
  3. 任务状态机 TaskState  —— 待审批 → 已批准 → 已执行 / 执行失败 / 已驳回 / 已撤销；
  4. 审计日志 ActionAudit  —— 谁、何时、依据什么、执行结果如何，全量留痕。

自主化分级（对应「前期问你，后期自己干」）
────────────────────────────────────────
  L0 仅建议     只生成待办，永不执行（纯参谋）
  L1 人工审批   每个动作都要人点「批准」（默认，最稳）
  L2 限额内自动 白名单动作在限额/配额内自动执行，超出转人工
  L3 白名单全自动 白名单动作只要过护栏就自动执行（成熟期）

写入权限闸门
────────
  自动执行能改哪类数据由 db_write_scope 控制：
  readonly（只读，全转人工）/ inventory（放行库存类）/ inventory+orders（放行订单类）。
  → 补库存：需开放库存写；
  → 发货 / 取消：需显式开放订单写（运单号 AUTO+时间戳 自动生成并写入审计）；
  → 退货：涉及资金流出，任何权限与等级下都永久人工。
  写通道不变：所有写入依然经 ZT-agent 接口执行，绝不直连业务表。
  本模块所有表都建在 agent_db 内、但均为**营销 Agent 自管表**，
  ZT-agent 的业务表依然只读（db.py 的 read_engine 守卫仍然生效）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index, Integer, String, Text,
    func, select, text as _sql,
)

from db import read_engine, write_engine
from drafts import OwnBase
from orchestrator import run_coordination_scan, init_coordination_tables
from zt_client import ZTAgentError, client as zt

# ════════════════════════════════════════════════════════════════
# 一、状态与等级常量
# ════════════════════════════════════════════════════════════════
STATUS_PENDING = "待审批"
STATUS_APPROVED = "已批准"
STATUS_DONE = "已执行"
STATUS_FAILED = "执行失败"
STATUS_REJECTED = "已驳回"
STATUS_CANCELLED = "已撤销"

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_DONE)

# 建单去重只针对「还没执行完」的在途任务：
#   · 待审批 / 已批准 → 同一动作同一目标不重复建单（避免巡检每次刷一堆重复待办）；
#   · 已执行**不参与**去重 → 否则「今天补 5 件、明天再补 5 件」会被误判成重复而拿回旧单。
DEDUPE_STATUSES = (STATUS_PENDING, STATUS_APPROVED)

# 执行期重复拦截的时间窗（秒）：同一动作+目标在这么短时间内被重复执行，判定为误双击
EXEC_DUPLICATE_WINDOW_SEC = 600

AUTONOMY_LEVELS: dict[str, tuple[str, str]] = {
    "L0": ("仅建议", "只生成待办，绝不自动执行——纯参谋模式"),
    "L1": ("人工审批", "每个动作都要人工点「批准」才执行（默认，最稳）"),
    "L2": ("限额内自动", "白名单动作在限额与配额内自动执行，超出自动转人工"),
    "L3": ("白名单全自动", "白名单动作只要通过护栏即自动执行，适合成熟期"),
}

RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}


# ════════════════════════════════════════════════════════════════
# 二、自管表模型
# ════════════════════════════════════════════════════════════════
class OperationTask(OwnBase):
    """运营任务单：AI 建议 / 人工发起 → 审批 → 执行，全程可追溯"""
    __tablename__ = "operation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_code = Column(String(30), index=True)          # restock / ship / ...
    action_name = Column(String(50), default="")
    title = Column(String(200), default="")
    reason = Column(Text, default="")                     # 为什么建议做（可解释性）
    payload = Column(Text, default="{}")                  # 动作参数（JSON）
    target_type = Column(String(20), default="")          # product / order
    target_id = Column(String(50), default="")            # product_id / order_no
    target_label = Column(String(120), default="")        # 可读名称，如「速攻2号」
    risk = Column(String(10), default="medium")
    quantity = Column(Integer, default=0)                 # 涉及数量（补货件数）
    amount = Column(Float, default=0.0)                   # 预估金额（元），限额判定用
    status = Column(String(20), default=STATUS_PENDING, index=True)
    autonomy_level = Column(String(4), default="L1")
    decision = Column(String(200), default="")            # 判定结论（人话）
    decision_checks = Column(Text, default="[]")          # 护栏逐条判定（JSON）
    auto_executed = Column(Boolean, default=False)        # 是否由系统自动执行
    idem_key = Column(String(120), index=True, default="")  # 幂等键，防重复执行
    source = Column(String(20), default="manual")         # manual / scan / diagnosis
    decided_by = Column(String(50), default="")
    decision_note = Column(String(200), default="")
    decided_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    exec_mode = Column(String(10), default="")            # live / dry_run
    exec_ok = Column(Boolean, nullable=True)
    exec_message = Column(String(300), default="")
    exec_response = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ActionAudit(OwnBase):
    """审计日志（只追加，不修改）"""
    __tablename__ = "action_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, index=True, default=0)
    action_code = Column(String(30), default="")
    event = Column(String(20), default="")     # created/approved/rejected/executed/failed/killed/...
    actor = Column(String(50), default="system")
    detail = Column(String(500), default="")
    created_at = Column(DateTime, default=datetime.now)


class AutonomySetting(OwnBase):
    """自主化策略（KV 表，单键单值，便于页面直接改）"""
    __tablename__ = "autonomy_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(50), unique=True, index=True)
    value = Column(String(200), default="")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AutopilotRun(OwnBase):
    """自动运营运行记录 —— 每次「无人值守巡检」留一条，便于复盘

    自动运营 = 定时（或手动）跑一轮完整闭环：巡检 → 建单 → 按护栏执行 → 出报告。
    它不新增任何权限：能自动执行什么，完全由自主化策略与七道护栏决定。
    """
    __tablename__ = "autopilot_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trigger = Column(String(20), default="timer")     # timer 定时 / manual 页面手动 / api 接口
    status = Column(String(20), default="ok")         # ok / failed
    message = Column(String(300), default="")
    scanned = Column(Text, default="{}")              # 本轮扫到的候选（JSON）
    created_count = Column(Integer, default=0)        # 新建待办数
    auto_count = Column(Integer, default=0)           # 自动执行数
    pending_count = Column(Integer, default=0)        # 转人工数
    hints_count = Column(Integer, default=0)          # 售后线索数
    coord_summary = Column(Text, default="")           # L5 跨 Agent 协调巡检摘要（JSON）
    exec_detail = Column(Text, nullable=True)          # 本轮涉及任务的业务明细（JSON：商品/客户/数量/金额/结果）
    started_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)


# ════════════════════════════════════════════════════════════════
# 三、动作注册表
# ════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ActionSpec:
    """一个「系统能做的事」的完整声明"""
    code: str
    name: str
    icon: str
    target_type: str          # product / order
    risk: str                 # low / medium / high
    reversible: bool          # 是否可逆
    involves_money: bool      # 是否涉及资金
    auto_eligible: bool       # 是否允许进入「自动执行」评估（可逆且不涉资金才可为 True）
    desc: str
    params: list[dict]
    # 幂等键只取「业务标识字段」——绝不能包含 reason / note 这类自由文本，
    # 否则同一笔业务只要备注写的不一样就能绕过防重复（本项目实际踩过）。
    idem_fields: tuple[str, ...] = ()
    limit_qty_key: str = ""   # 数量上限策略键（L2 判定用）
    limit_amount_key: str = ""  # 金额上限策略键（L2 判定用）


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "restock": ActionSpec(
        code="restock", name="补充库存", icon="📦", target_type="product",
        risk="medium", reversible=True, involves_money=True, auto_eligible=True,
        desc="把缺货/低于预警线的商品库存补到安全水位。库存可再次调整，属可逆动作。",
        params=[
            {"key": "product_id", "label": "商品ID", "type": "int", "required": True},
            {"key": "adjust", "label": "补货数量", "type": "int", "required": True},
            {"key": "reason", "label": "补货说明", "type": "str", "required": False},
        ],
        idem_fields=("product_id", "adjust"),
        limit_qty_key="auto_restock_max_qty",
        limit_amount_key="auto_restock_max_amount",
    ),
    "ship": ActionSpec(
        code="ship", name="订单发货", icon="🚚", target_type="order",
        risk="medium", reversible=False, involves_money=False, auto_eligible=False,
        desc="为已付款订单发货并填运单号。发货会扣减库存且不可撤回，必须人工确认。",
        params=[
            {"key": "order_no", "label": "订单号", "type": "str", "required": True},
            {"key": "ship_company", "label": "快递公司", "type": "str", "required": True},
            {"key": "tracking_no", "label": "运单号", "type": "str", "required": True},
        ],
        idem_fields=("order_no",),
    ),
    "return": ActionSpec(
        code="return", name="退货处理", icon="↩️", target_type="order",
        risk="high", reversible=False, involves_money=True, auto_eligible=False,
        desc="确认客户退货：库存回滚 + 钱包自动退款。涉及资金流出，必须人工审批。",
        params=[
            {"key": "order_no", "label": "订单号", "type": "str", "required": True},
            {"key": "reason", "label": "退货原因", "type": "str", "required": False},
        ],
        idem_fields=("order_no",),
    ),
    "cancel": ActionSpec(
        code="cancel", name="取消订单", icon="🚫", target_type="order",
        risk="high", reversible=False, involves_money=False, auto_eligible=False,
        desc="取消未发货订单并释放预占库存。影响客户体验，必须人工确认。",
        params=[
            {"key": "order_no", "label": "订单号", "type": "str", "required": True},
            {"key": "reason", "label": "取消原因", "type": "str", "required": False},
        ],
        idem_fields=("order_no",),
    ),
}


def action_catalog() -> list[dict]:
    """动作目录（前端渲染用）"""
    out = []
    for spec in ACTION_REGISTRY.values():
        out.append({
            "code": spec.code, "name": spec.name, "icon": spec.icon,
            "target_type": spec.target_type, "risk": spec.risk,
            "risk_label": RISK_LABELS.get(spec.risk, spec.risk),
            "reversible": spec.reversible, "involves_money": spec.involves_money,
            "auto_eligible": spec.auto_eligible, "desc": spec.desc, "params": spec.params,
        })
    return out


# ════════════════════════════════════════════════════════════════
# 四、策略（自主化配置）
# ════════════════════════════════════════════════════════════════
POLICY_DEFAULTS: dict[str, str] = {
    "global_level": "L1",            # L0 / L1 / L2 / L3
    "kill_switch": "off",            # off / on  —— 急停：开启后所有动作转人工
    "execute_mode": "live",          # live / dry_run —— 真实执行 / 演练
    "auto_actions": "restock",       # 允许进入自动评估的动作（逗号分隔，取值=动作注册表 code）
    "auto_target_whitelist": "",     # 目标白名单（商品ID或订单号，逗号分隔；空=不限制）
    # ── 数据库写入权限（写动作依然全部经 ZT-agent 接口，绝不直连业务表）──
    # readonly=只读（全部写动作转人工）/ inventory=开放库存写 / inventory+orders=库存+订单写
    "db_write_scope": "readonly",
    "default_ship_company": "",      # 自动发货时使用的默认快递公司（留空=中通快递）
    "auto_restock_max_qty": "20",    # L2：单次自动补货数量上限
    "auto_restock_max_amount": "500",  # L2：单次自动补货金额上限（元）
    "daily_auto_quota": "10",        # 每日自动执行次数上限（所有等级生效）
    # ── 自动运营（无人值守）──
    "autopilot_enabled": "off",      # on / off —— 是否开启定时自动巡检
    "autopilot_interval_minutes": "30",  # 自动巡检间隔（分钟）
    # ── L5 多智能体编排：跨 Agent 协调 ──
    "coord_enabled": "off",          # on / off —— 总开关（关掉即整体停用跨 Agent 协调）
    "coord_auto_apply": "off",       # on / off —— on=自动发往 ZT；off=只建议、转人工确认
    "coord_return_surge_threshold": "20",  # 退货率阈值（%），超过即标记「需复盘」
}

POLICY_LABELS = {
    "global_level": "自主等级",
    "kill_switch": "急停开关",
    "execute_mode": "执行模式",
    "auto_actions": "允许自动执行的动作",
    "auto_target_whitelist": "目标白名单",
    "db_write_scope": "数据库写入权限",
    "default_ship_company": "默认快递公司",
    "auto_restock_max_qty": "单次自动补货上限(件)",
    "auto_restock_max_amount": "单次自动补货上限(元)",
    "daily_auto_quota": "每日自动执行配额(次)",
    "autopilot_enabled": "自动运营开关",
    "autopilot_interval_minutes": "自动巡检间隔(分钟)",
    "coord_enabled": "跨Agent协调总开关",
    "coord_auto_apply": "协调自动下发ZT",
    "coord_return_surge_threshold": "退货率告警阈值(%)",
}


def get_policy(db) -> dict:
    """读取策略（缺失项回落到默认值）"""
    policy = dict(POLICY_DEFAULTS)
    rows = db.execute(select(AutonomySetting)).scalars().all()
    for r in rows:
        if r.key in POLICY_DEFAULTS:
            policy[r.key] = (r.value or "").strip()
    # 归一化：等级/开关/模式非法值直接回落，避免脏配置把系统卡死
    if policy["global_level"] not in AUTONOMY_LEVELS:
        policy["global_level"] = POLICY_DEFAULTS["global_level"]
    if policy["kill_switch"] not in ("on", "off"):
        policy["kill_switch"] = "off"
    if policy["execute_mode"] not in ("live", "dry_run"):
        policy["execute_mode"] = "live"
    if policy["db_write_scope"] not in ("readonly", "inventory", "inventory+orders"):
        policy["db_write_scope"] = "readonly"
    # 动作白名单归一化：只保留注册表里真实存在的 code，去重、去空白
    _known = [a.strip() for a in (policy["auto_actions"] or "").split(",") if a.strip()]
    policy["auto_actions"] = ",".join(dict.fromkeys(a for a in _known if a in ACTION_REGISTRY))
    return policy


def set_policy(db, updates: dict, actor: str = "运营") -> dict:
    """写入策略（只接受已知键）"""
    changed = []
    for k, v in updates.items():
        if k not in POLICY_DEFAULTS or v is None:
            continue
        v = str(v).strip()
        row = db.execute(select(AutonomySetting).where(AutonomySetting.key == k)).scalar_one_or_none()
        if row:
            if (row.value or "") != v:
                row.value = v
                changed.append(f"{POLICY_LABELS.get(k, k)}: {v}")
        else:
            db.add(AutonomySetting(key=k, value=v))
            changed.append(f"{POLICY_LABELS.get(k, k)}: {v}")
    if changed:
        db.add(ActionAudit(task_id=0, action_code="policy", event="policy_changed",
                           actor=actor, detail="；".join(changed)[:500]))
    db.commit()
    return get_policy(db)


def _limit(policy: dict, key: str, default: float) -> float:
    try:
        return float(policy.get(key) or default)
    except (TypeError, ValueError):
        return default


def today_auto_count(db) -> int:
    """今日已自动执行次数（配额判定）"""
    today = date.today()
    start = datetime(today.year, today.month, today.day)
    return int(db.execute(
        select(func.count(OperationTask.id)).where(
            OperationTask.auto_executed.is_(True),
            OperationTask.created_at >= start,
        )
    ).scalar() or 0)


# ════════════════════════════════════════════════════════════════
# 五、护栏引擎：这一次该自动，还是等人点头？
# ════════════════════════════════════════════════════════════════
@dataclass
class Decision:
    auto: bool
    level: str
    reason: str
    checks: list = field(default_factory=list)   # [{name, passed, detail}]


def decide(spec: ActionSpec, payload: dict, quantity: int, amount: float,
           policy: dict, today_auto: int) -> Decision:
    """护栏逐条判定。

    顺序即优先级：任何一条不通过都直接转人工，并把「为什么」写进 checks，
    前端会原样展示给运营人员 —— 让「AI 为什么不敢自己动手」变得可解释。
    """
    checks: list[dict] = []
    level = policy["global_level"]

    def chk(name: str, passed: bool, detail: str) -> bool:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        return bool(passed)

    # 1) 急停开关 —— 最高优先级
    if not chk("急停开关", policy["kill_switch"] != "on",
               "急停已开启，全部动作转人工" if policy["kill_switch"] == "on" else "未开启"):
        return Decision(False, level, "急停已开启，需人工审批", checks)

    # 2) 自主等级
    if not chk("自主等级", level != "L0",
               "L0 仅建议：只生成待办，不自动执行" if level == "L0"
               else f"当前 {level} {AUTONOMY_LEVELS[level][0]}"):
        return Decision(False, level, "L0 仅建议模式，需人工执行", checks)

    # 3) 数据库写入权限 + 动作性质
    #    · 涉资金且不可逆（退货）→ 永久人工，任何权限/等级都放不开
    #    · 权限=只读 → 所有写动作转人工（等价于整体只出建议）
    #    · 订单类动作（发货/取消）需权限显式包含 orders；开放后原「永久人工」动作可进入自动评估
    #    写入通道不变：全部经 ZT-agent 接口执行，本权限只控制「自动执行允许碰哪类数据」
    scope = policy.get("db_write_scope") or "readonly"
    if not spec.auto_eligible and spec.involves_money:
        if not chk("动作可自动化", False,
                   f"「{spec.name}」涉及资金流出，任何权限与等级下都必须人工审批"):
            return Decision(False, level, "动作性质要求人工审批", checks)
    scope_ok = scope != "readonly" and (spec.target_type != "order" or "orders" in scope)
    if not chk("数据库写入权限", scope_ok,
               ("权限=只读：所有写动作转人工" if scope == "readonly"
                else f"权限仅开放库存写入：「{spec.name}」属订单类动作，转人工")
               if not scope_ok else
               (f"已开放订单写入：「{spec.name}」允许进入自动评估（运单号自动生成）"
                if not spec.auto_eligible
                else f"已开放{'库存+订单' if 'orders' in scope else '库存'}写入权限")):
        return Decision(False, level,
                        "只读权限，需人工执行" if scope == "readonly"
                        else "未开放订单写入权限，需人工执行", checks)
    if not chk("动作可自动化", True,
               f"「{spec.name}」原设计为人工审批，已通过订单写入权限显式放开"
               if not spec.auto_eligible else "可逆且不涉资金的动作为可自动化"):
        return Decision(False, level, "动作性质要求人工审批", checks)

    # 4) 动作白名单
    auto_actions = [a.strip() for a in (policy["auto_actions"] or "").split(",") if a.strip()]
    if not chk("动作白名单", spec.code in auto_actions,
               f"「{spec.name}」已在自动白名单" if spec.code in auto_actions
               else f"「{spec.name}」不在自动白名单（当前白名单：{'、'.join(auto_actions) or '空'}）"):
        return Decision(False, level, "动作不在自动白名单", checks)

    # 5) 等级 L1：一律人工
    if not chk("等级策略", level != "L1",
               "L1 人工审批：全部动作需人工确认" if level == "L1" else f"{level} 允许在护栏内自动执行"):
        return Decision(False, level, "L1 人工审批模式", checks)

    # 6) 目标白名单（空 = 不限制）
    wl = [x.strip() for x in (policy["auto_target_whitelist"] or "").split(",") if x.strip()]
    target_id = str(payload.get("product_id") or payload.get("order_no") or "")
    if wl:
        if not chk("目标白名单", target_id in wl,
                   f"{target_id} 在白名单内" if target_id in wl
                   else f"{target_id} 不在白名单（{len(wl)} 个已授权目标）"):
            return Decision(False, level, "目标不在白名单", checks)
    else:
        chk("目标白名单", True, "未设置白名单，不限制目标")

    # 7) 限额判定（L2 卡限额；L3 放宽限额）
    if level == "L2":
        if spec.limit_qty_key:
            cap = _limit(policy, spec.limit_qty_key, 20)
            if not chk("数量限额", quantity <= cap,
                       f"本次 {quantity} 件 ≤ 上限 {cap:g} 件" if quantity <= cap
                       else f"本次 {quantity} 件 > 上限 {cap:g} 件，超出限额转人工"):
                return Decision(False, level, "超出自动执行限额", checks)
        if spec.limit_amount_key:
            cap = _limit(policy, spec.limit_amount_key, 500)
            if not chk("金额限额", amount <= cap,
                       f"预估 ¥{amount:.2f} ≤ 上限 ¥{cap:.2f}" if amount <= cap
                       else f"预估 ¥{amount:.2f} > 上限 ¥{cap:.2f}，超出限额转人工"):
                return Decision(False, level, "超出自动执行限额", checks)
    else:
        chk("限额判定", True, f"{level} 不应用单次限额（仍受每日配额约束）")

    # 8) 每日配额
    quota = int(_limit(policy, "daily_auto_quota", 10))
    if not chk("每日配额", today_auto < quota,
               f"今日已自动执行 {today_auto} 次，配额 {quota} 次" if today_auto < quota
               else f"今日自动执行已达配额 {quota} 次，转人工"):
        return Decision(False, level, "今日自动配额已用完", checks)

    return Decision(True, level, "全部护栏通过，可自动执行", checks)


# ════════════════════════════════════════════════════════════════
# 六、任务生命周期
# ════════════════════════════════════════════════════════════════
def _audit(db, task_id: int, action_code: str, event: str, actor: str, detail: str = "") -> None:
    db.add(ActionAudit(task_id=task_id, action_code=action_code, event=event,
                       actor=actor or "system", detail=(detail or "")[:500]))


def _biz_ctx(spec: "ActionSpec", payload: dict, quantity: int = 0, amount: float = 0.0) -> str:
    """把任务的业务上下文拼成一句人话（商品/订单/客户/数量/金额），用于审计留痕。

    上下文字段（product_name/customer_name/phone/quantity/total_price）由巡检建单时
    写进 payload，人工建单没有这些字段时自动回落到 ID/数量/金额，保证不空泛。
    """
    parts: list[str] = []
    if payload.get("product_name"):
        parts.append(f"商品「{payload['product_name']}」")
    elif spec.target_type == "product" and payload.get("product_id"):
        parts.append(f"商品#{payload['product_id']}")
    if payload.get("order_no"):
        who = payload.get("customer_name") or "客户"
        if payload.get("phone"):
            who += f" {payload['phone']}"
        parts.append(f"订单 {payload['order_no']}（{who}）")
    try:
        qty = int(payload.get("quantity") or quantity or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty:
        parts.append(f"{qty} 件")
    amt = payload.get("total_price") or amount
    try:
        amt_f = float(amt or 0)
    except (TypeError, ValueError):
        amt_f = 0.0
    if amt_f:
        parts.append(f"¥{amt_f:.2f}")
    return " · ".join(parts)


def _idem_key(spec: ActionSpec, target_id: str, payload: dict) -> str:
    """幂等键：只取业务标识字段（如 product_id+adjust / order_no），忽略备注类自由文本。"""
    fields = spec.idem_fields or tuple(payload.keys())
    core = {k: payload.get(k) for k in fields}
    raw = json.dumps(core, sort_keys=True, ensure_ascii=False, default=str)
    return f"{spec.code}:{target_id}:{hashlib.md5(raw.encode('utf-8')).hexdigest()[:10]}"


def _payload_of(task: OperationTask) -> dict:
    try:
        return json.loads(task.payload or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def task_dict(t: OperationTask) -> dict:
    spec = ACTION_REGISTRY.get(t.action_code)
    try:
        checks = json.loads(t.decision_checks or "[]")
    except (json.JSONDecodeError, TypeError):
        checks = []
    try:
        response = json.loads(t.exec_response or "{}")
    except (json.JSONDecodeError, TypeError):
        response = {}
    return {
        "id": t.id,
        "action_code": t.action_code,
        "action_name": t.action_name or (spec.name if spec else t.action_code),
        "icon": spec.icon if spec else "🔧",
        "title": t.title,
        "reason": t.reason,
        "payload": _payload_of(t),
        "target_type": t.target_type,
        "target_id": t.target_id,
        "target_label": t.target_label,
        "risk": t.risk,
        "risk_label": RISK_LABELS.get(t.risk, t.risk),
        "quantity": t.quantity or 0,
        "amount": round(float(t.amount or 0), 2),
        "status": t.status,
        "autonomy_level": t.autonomy_level,
        "level_label": AUTONOMY_LEVELS.get(t.autonomy_level, ("", ""))[0],
        "decision": t.decision,
        "decision_checks": checks,
        "auto_executed": bool(t.auto_executed),
        "source": t.source,
        "decided_by": t.decided_by,
        "decision_note": t.decision_note,
        "decided_at": t.decided_at.strftime("%Y-%m-%d %H:%M") if t.decided_at else "",
        "executed_at": t.executed_at.strftime("%Y-%m-%d %H:%M") if t.executed_at else "",
        "exec_mode": t.exec_mode,
        "exec_ok": t.exec_ok,
        "exec_message": t.exec_message,
        "exec_response": response,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
    }


def create_task(db, *, action_code: str, payload: dict, reason: str = "",
                title: str = "", target_label: str = "", quantity: int = 0,
                amount: float = 0.0, source: str = "manual",
                actor: str = "运营", auto_run: bool = True) -> OperationTask:
    """建任务单 → 过护栏 → 需要人工就挂「待审批」，允许自动就直接执行。

    幂等：同一动作 + 同一目标 + 同样参数，若已有「待审批/已批准/已执行」的任务，
    则不再重复创建（防止巡检每次跑都刷一堆重复待办）。
    """
    spec = ACTION_REGISTRY.get(action_code)
    if spec is None:
        raise ValueError(f"未知动作：{action_code}")

    target_id = str(payload.get("product_id") or payload.get("order_no") or "")
    idem = _idem_key(spec, target_id, payload)
    dup = db.execute(
        select(OperationTask).where(
            OperationTask.idem_key == idem,
            OperationTask.status.in_(DEDUPE_STATUSES),
        ).limit(1)
    ).scalar_one_or_none()
    if dup is not None:
        dup.is_new = False          # type: ignore[attr-defined]  —— 供巡检统计"新增/已存在"
        return dup

    policy = get_policy(db)
    decision = decide(spec, payload, quantity, amount, policy, today_auto_count(db))

    # 标题兜底：没给可读名称时，用「商品#ID」「订单号」这类有意义的标签，避免出现「补充库存 · 3」
    if not target_label:
        target_label = (f"商品#{target_id}" if spec.target_type == "product"
                        else f"订单 {target_id}" if target_id else "")

    task = OperationTask(
        action_code=spec.code,
        action_name=spec.name,
        title=title or f"{spec.name} · {target_label or target_id}",
        reason=reason,
        payload=json.dumps(payload, ensure_ascii=False),
        target_type=spec.target_type,
        target_id=target_id,
        target_label=target_label,
        risk=spec.risk,
        quantity=quantity,
        amount=amount,
        status=STATUS_PENDING,
        autonomy_level=decision.level,
        decision=decision.reason,
        decision_checks=json.dumps(decision.checks, ensure_ascii=False),
        idem_key=idem,
        source=source,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task.is_new = True              # type: ignore[attr-defined]
    _ctx = _biz_ctx(spec, payload, quantity, amount)
    _audit(db, task.id, spec.code, "created", actor,
           f"来源 {source}；{_ctx}；判定：{decision.reason}" if _ctx
           else f"来源 {source}；判定：{decision.reason}")
    db.commit()

    if decision.auto and auto_run:
        task.status = STATUS_APPROVED
        task.decided_by = "AI 自动（护栏内）"
        task.decision_note = decision.reason
        task.decided_at = datetime.now()
        db.commit()
        execute_task(db, task, actor="AI 自动", auto=True)
        db.refresh(task)
    return task


def approve_task(db, task: OperationTask, actor: str, note: str = "",
                 execute_now: bool = True) -> OperationTask:
    """人工批准（可顺便立即执行）"""
    if task.status not in (STATUS_PENDING,):
        raise ValueError(f"当前状态「{task.status}」不可批准")
    task.status = STATUS_APPROVED
    task.decided_by = actor or "运营"
    task.decision_note = (note or "人工批准")[:200]
    task.decided_at = datetime.now()
    db.commit()
    _audit(db, task.id, task.action_code, "approved", actor, note or "人工批准")
    db.commit()
    if execute_now:
        execute_task(db, task, actor=actor, auto=False)
        db.refresh(task)
    return task


def reject_task(db, task: OperationTask, actor: str, note: str = "") -> OperationTask:
    if task.status not in (STATUS_PENDING, STATUS_APPROVED):
        raise ValueError(f"当前状态「{task.status}」不可驳回")
    task.status = STATUS_REJECTED
    task.decided_by = actor or "运营"
    task.decision_note = (note or "人工驳回")[:200]
    task.decided_at = datetime.now()
    db.commit()
    _audit(db, task.id, task.action_code, "rejected", actor, note or "人工驳回")
    db.commit()
    return task


def cancel_task(db, task: OperationTask, actor: str, note: str = "") -> OperationTask:
    if task.status in (STATUS_DONE, STATUS_CANCELLED):
        raise ValueError(f"当前状态「{task.status}」不可撤销")
    task.status = STATUS_CANCELLED
    task.decision_note = (note or "人工撤销")[:200]
    db.commit()
    _audit(db, task.id, task.action_code, "cancelled", actor, note or "人工撤销")
    db.commit()
    return task


def update_payload(db, task: OperationTask, patch: dict) -> OperationTask:
    """待审批阶段补充/修改参数（典型场景：发货前填运单号）"""
    if task.status != STATUS_PENDING:
        raise ValueError(f"仅「待审批」状态可修改参数，当前「{task.status}」")
    spec = ACTION_REGISTRY.get(task.action_code)
    allowed = {p["key"] for p in spec.params} if spec else set()
    payload = _payload_of(task)
    changed = []
    for k, v in (patch or {}).items():
        if k in allowed and v is not None and str(v) != "":
            payload[k] = v
            changed.append(k)
    task.payload = json.dumps(payload, ensure_ascii=False)
    db.commit()
    _audit(db, task.id, task.action_code, "updated", "运营", f"补充参数：{'、'.join(changed) or '无'}")
    db.commit()
    return task


# ── 执行：把任务单翻译成对 ZT-agent 的真实调用 ──────────────────
def _dispatch(spec: ActionSpec, payload: dict) -> dict:
    """动作 → ZT-agent 接口调用（这里是"营销 Agent 的手脚"唯一出口）"""
    if spec.code == "restock":
        return zt.restock(int(payload["product_id"]), int(payload["adjust"]),
                          str(payload.get("reason") or "营销侧智能补货"))
    if spec.code == "ship":
        return zt.ship(str(payload["order_no"]), str(payload.get("ship_company") or ""),
                       str(payload.get("tracking_no") or ""))
    if spec.code == "return":
        return zt.return_order(str(payload["order_no"]), str(payload.get("reason") or "客户退货"))
    if spec.code == "cancel":
        return zt.cancel_order(str(payload["order_no"]), str(payload.get("reason") or "营销侧取消"))
    raise ValueError(f"动作 {spec.code} 未实现执行器")


def _validate_payload(spec: ActionSpec, payload: dict) -> str:
    """执行前参数校验，返回错误信息（空串=通过）"""
    for p in spec.params:
        if p.get("required") and not str(payload.get(p["key"]) or "").strip():
            return f"缺少必填参数：{p['label']}（{p['key']}）"
    return ""


def execute_task(db, task: OperationTask, actor: str, auto: bool = False) -> OperationTask:
    """执行任务：调 ZT-agent 接口 → 回写结果 → 记审计"""
    spec = ACTION_REGISTRY.get(task.action_code)
    if spec is None:
        raise ValueError(f"未知动作：{task.action_code}")
    if task.status != STATUS_APPROVED:
        raise ValueError(f"仅「已批准」状态可执行，当前「{task.status}」")

    # 重复执行兜底：同一动作 + 同一目标在短时间窗内已成功执行过 → 判定为误双击并拦截。
    # 用时间窗而不是「永久去重」，是为了不挡住合法的重复业务（今天补 5 件、明天再补 5 件）。
    window_start = datetime.now() - timedelta(seconds=EXEC_DUPLICATE_WINDOW_SEC)
    done = db.execute(
        select(OperationTask).where(
            OperationTask.idem_key == task.idem_key,
            OperationTask.status == STATUS_DONE,
            OperationTask.id != task.id,
            OperationTask.executed_at.is_not(None),
            OperationTask.executed_at >= window_start,
        ).limit(1)
    ).scalar_one_or_none()
    if done is not None:
        task.status = STATUS_FAILED
        task.exec_ok = False
        task.exec_message = (
            f"疑似重复提交：任务 #{done.id} 在 {EXEC_DUPLICATE_WINDOW_SEC // 60} 分钟内"
            f"已成功执行过同样动作，已拦截（如需重复执行请稍后或调整参数）"
        )
        db.commit()
        _audit(db, task.id, task.action_code, "failed", actor, task.exec_message)
        db.commit()
        return task

    payload = _payload_of(task)
    # 发货任务自动补全：默认快递公司取策略项，运单号按 AUTO+时间戳生成
    # （自动执行的任务单不会有人工填单环节，缺这两项会被必填校验拦死）
    if spec.code == "ship":
        _pol = get_policy(db)
        if not str(payload.get("ship_company") or "").strip():
            payload["ship_company"] = (_pol.get("default_ship_company") or "").strip() or "中通快递"
        if not str(payload.get("tracking_no") or "").strip():
            payload["tracking_no"] = "AUTO" + datetime.now().strftime("%Y%m%d%H%M%S")
    bad = _validate_payload(spec, payload)
    if bad:
        task.status = STATUS_FAILED
        task.exec_ok = False
        task.exec_message = bad
        db.commit()
        _audit(db, task.id, task.action_code, "failed", actor, bad)
        db.commit()
        return task

    # 执行模式以「策略表」为准（页面上改完立刻生效），环境变量仅作兜底
    zt.mode_override = (get_policy(db).get("execute_mode") or "").strip()

    try:
        result = _dispatch(spec, payload)
    except ZTAgentError as e:                 # 网络/鉴权类错误
        task.status = STATUS_FAILED
        task.exec_ok = False
        task.exec_message = str(e)[:300]
        task.exec_mode = zt.mode
        db.commit()
        _audit(db, task.id, task.action_code, "failed", actor, str(e))
        db.commit()
        return task

    ok = bool(result.get("ok"))
    dry = bool(result.get("dry_run"))
    task.exec_mode = "dry_run" if dry else zt.mode
    task.exec_ok = ok
    task.exec_message = str(result.get("message") or "")[:300]
    task.exec_response = json.dumps(
        {"status": result.get("status"), "data": result.get("data")}, ensure_ascii=False
    )[:4000]
    task.executed_at = datetime.now()
    # 演练模式不改变业务结果，任务回到「已批准」，方便切到 live 后真正执行
    task.status = STATUS_APPROVED if dry else (STATUS_DONE if ok else STATUS_FAILED)
    if auto:
        task.auto_executed = True
    db.commit()
    # 执行审计带上完整业务上下文：发了什么货、发给谁、多少件、多少钱、运单号
    _ctx = _biz_ctx(spec, payload, task.quantity, task.amount)
    if spec.code == "ship":
        _ctx += (f"；快递 {payload.get('ship_company')} / 运单号 {payload.get('tracking_no')}"
                 if _ctx else
                 f"快递 {payload.get('ship_company')} / 运单号 {payload.get('tracking_no')}")
    _audit(db, task.id, task.action_code, "executed" if ok else "failed", actor,
           f"[{task.exec_mode}] {task.exec_message}" + (f"（{_ctx}）" if _ctx else ""))
    db.commit()
    return task


# ════════════════════════════════════════════════════════════════
# 七、智能巡检：读真实业务数据 → 产出候选待办
# ════════════════════════════════════════════════════════════════
AUTO_RESTOCK_SAFE_MULTIPLIER = 2      # 安全库存 = 预警线 × 2
AFTER_SALE_KEYWORDS = ("退货", "退款", "换货", "坏了", "破损", "质量", "投诉", "少发", "错发", "没收到")


def scan_candidates(read_db) -> dict:
    """只读扫描 ZT-agent 业务数据，产出三类候选：
       restock —— 需要补货的商品（库存可用量 ≤ 预警线）
       ship    —— 待发货订单
       hints   —— 售后线索（客户咨询里提到退换货/质量问题，供人工核实）
    """
    from models_readonly import Conversations, Inventory, Orders, Products

    restock: list[dict] = []
    rows = read_db.execute(
        select(Inventory, Products).join(Products, Products.id == Inventory.product_id)
        .where(Products.is_active.is_(True))
        .order_by(Inventory.product_id)
    ).all()
    for inv, prod in rows:
        stock = int(inv.stock or 0)
        reserved = int(inv.reserved_stock or 0)
        available = max(0, stock - reserved)
        alert = int(inv.alert_line or 0)
        if available > alert:
            continue
        safe_level = max(alert * AUTO_RESTOCK_SAFE_MULTIPLIER, alert + 10)
        qty = max(safe_level - stock, 10)
        unit_cost = float(prod.wholesale_price or 0)
        restock.append({
            "product_id": prod.id,
            "product_name": prod.name,
            "sku": prod.sku or "",
            "stock": stock,
            "reserved": reserved,
            "available": available,
            "alert_line": alert,
            "suggest_qty": qty,
            "unit_cost": round(unit_cost, 2),
            "est_amount": round(qty * unit_cost, 2),
            "severity": "缺货" if available <= 0 else ("紧张" if available < alert else "踩线"),
            "reason": (
                f"「{prod.name}」可用库存 {available} 件，"
                f"{'已售罄' if available <= 0 else f'低于/等于预警线 {alert} 件'}；"
                f"按安全库存 {safe_level} 件测算，建议补货 {qty} 件（预估成本 ¥{qty * unit_cost:.2f}）"
            ),
        })

    ship: list[dict] = []
    pending = read_db.execute(
        select(Orders).where(Orders.status == "未发货").order_by(Orders.created_at.desc()).limit(50)
    ).scalars().all()
    for o in pending:
        created = o.created_at.strftime("%Y-%m-%d %H:%M") if o.created_at else ""
        ship.append({
            "order_no": o.order_no,
            "customer_name": o.customer_name or "",
            "phone": o.phone or "",
            "product_name": o.product_name or "",
            "quantity": int(o.quantity or 1),
            "total_price": round(float(o.total_price or 0), 2),
            "created_at": created,
            "reason": (
                f"订单 {o.order_no}（{o.customer_name or '客户'} × {o.product_name or '商品'} "
                f"{int(o.quantity or 1)} 件，¥{float(o.total_price or 0):.2f}）尚未发货，"
                f"下单时间 {created}，建议尽快安排发货并回填运单号"
            ),
        })

    hints: list[dict] = []
    since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    recent = read_db.execute(
        select(Conversations.content, Conversations.created_at)
        .where(Conversations.role == "user", Conversations.created_at >= since)
        .order_by(Conversations.created_at.desc()).limit(200)
    ).all()
    for content, created in recent:
        text = content or ""
        hit = [k for k in AFTER_SALE_KEYWORDS if k in text]
        if not hit:
            continue
        hints.append({
            "content": text[:150],
            "keywords": hit,
            "created_at": created.strftime("%Y-%m-%d %H:%M") if created else "",
        })
        if len(hints) >= 10:
            break

    return {"restock": restock, "ship": ship, "hints": hints}


def create_tasks_from_scan(write_db, read_db, actor: str = "系统巡检") -> dict:
    """巡检 → 建任务单（是否自动执行由护栏决定；重复候选自动去重）"""
    cand = scan_candidates(read_db)
    created_ids, existing_ids, auto_ids = [], [], []

    def _track(task: OperationTask) -> None:
        if not getattr(task, "is_new", False):
            existing_ids.append(task.id)
        elif task.auto_executed:
            auto_ids.append(task.id)
        else:
            created_ids.append(task.id)

    for item in cand["restock"]:
        task = create_task(
            write_db,
            action_code="restock",
            payload={"product_id": item["product_id"], "adjust": item["suggest_qty"],
                     "product_name": item["product_name"],
                     "reason": f"智能补货：{item['severity']}（可用 {item['available']} / 预警线 {item['alert_line']}）"},
            reason=item["reason"],
            title=f"补货 · {item['product_name']} +{item['suggest_qty']} 件",
            target_label=item["product_name"],
            quantity=item["suggest_qty"],
            amount=item["est_amount"],
            source="scan",
            actor=actor,
        )
        _track(task)

    for item in cand["ship"]:
        task = create_task(
            write_db,
            action_code="ship",
            payload={"order_no": item["order_no"], "ship_company": "", "tracking_no": "",
                     "customer_name": item.get("customer_name", ""),
                     "phone": item.get("phone", ""),
                     "product_name": item.get("product_name", ""),
                     "quantity": item.get("quantity", 1),
                     "total_price": item.get("total_price", 0)},
            reason=item["reason"],
            title=f"发货 · {item['order_no']}（{item['product_name']}）",
            target_label=item["order_no"],
            quantity=item["quantity"],
            amount=item["total_price"],
            source="scan",
            actor=actor,
        )
        _track(task)

    return {
        "scanned": {"restock_candidates": len(cand["restock"]),
                    "ship_candidates": len(cand["ship"]),
                    "after_sale_hints": len(cand["hints"])},
        "created_task_ids": created_ids,
        "auto_executed_task_ids": auto_ids,
        "skipped_existing_task_ids": existing_ids,
        "hints": cand["hints"],
        "restock_preview": cand["restock"],
        "ship_preview": cand["ship"],
    }


# ════════════════════════════════════════════════════════════════
# 九、自动运营（无人值守）：定时跑一轮完整闭环并出报告
# ════════════════════════════════════════════════════════════════
def run_autopilot(write_db, read_db, trigger: str = "timer") -> dict:
    """跑一轮自动运营：巡检 → 建单 → （按护栏）执行 → 出报告。

    它**不新增任何权限** —— 能自动执行什么完全由自主化策略与七道护栏决定；
    急停开启时照样巡检、照样生成待办，只是所有动作都转为人工审批。
    每一轮都会在 `autopilot_runs` 留一条记录，便于复盘「它到底干了什么」。
    """
    run = AutopilotRun(trigger=trigger, started_at=datetime.now())
    write_db.add(run)
    write_db.commit()
    write_db.refresh(run)

    try:
        result = create_tasks_from_scan(write_db, read_db, actor=f"自动运营({trigger})")
    except Exception as e:  # 任何异常都不能让调度器崩掉
        run.status = "failed"
        run.message = f"{type(e).__name__}: {e}"[:300]
        run.finished_at = datetime.now()
        write_db.commit()
        return {"success": False, "message": run.message, "run_id": run.id}

    scanned = result.get("scanned", {})
    created_ids = result.get("created_task_ids", [])
    auto_ids = result.get("auto_executed_task_ids", [])
    hints = result.get("hints", [])
    policy = get_policy(write_db)
    killed = policy.get("kill_switch") == "on"

    # ── L5 多智能体编排：跨 Agent 协调巡检（独立 try，失败不拖垮整轮）──
    coord_summary = None
    try:
        coord_summary = run_coordination_scan(
            write_db, read_db, policy, actor=f"自动运营({trigger})"
        )
    except Exception as e:  # 编排异常隔离，主闭环照常出报告
        coord_summary = {
            "enabled": (policy.get("coord_enabled") == "on"),
            "error": f"{type(e).__name__}: {e}",
        }

    run.scanned = json.dumps(scanned, ensure_ascii=False)
    run.created_count = len(created_ids)
    run.auto_count = len(auto_ids)
    run.pending_count = len(created_ids)
    run.hints_count = len(hints)
    run.coord_summary = json.dumps(coord_summary, ensure_ascii=False, default=str)

    # 本轮涉及任务的业务明细：发了什么货、发给谁、多少件、结果如何（供自动运营记录面板展示）
    detail_rows: list[dict] = []
    if created_ids or auto_ids:
        trows = write_db.execute(
            select(OperationTask).where(OperationTask.id.in_([*created_ids, *auto_ids]))
        ).scalars().all()
        for t in trows:
            try:
                p = json.loads(t.payload or "{}")
            except json.JSONDecodeError:
                p = {}
            detail_rows.append({
                "task_id": t.id,
                "icon": (ACTION_REGISTRY.get(t.action_code).icon
                         if t.action_code in ACTION_REGISTRY else "•"),
                "action": t.action_name,
                "title": t.title,
                "customer": p.get("customer_name") or "",
                "product": p.get("product_name") or t.target_label or "",
                "qty": int(t.quantity or 0),
                "amount": round(float(t.amount or 0), 2),
                "auto": bool(t.auto_executed),
                "status": t.status,
                "result": (t.exec_message or "")[:120],
                "time": (t.executed_at or t.created_at).strftime("%m-%d %H:%M")
                        if (t.executed_at or t.created_at) else "",
            })
    run.exec_detail = json.dumps(detail_rows, ensure_ascii=False)
    run.status = "ok"
    run.finished_at = datetime.now()
    if killed:
        run.message = "急停开启：本次仅巡检并生成待办，未自动执行任何动作"
    elif auto_ids:
        run.message = f"自动执行 {len(auto_ids)} 条，转人工 {len(created_ids)} 条"
    else:
        run.message = f"本轮无需自动执行，生成待办 {len(created_ids)} 条"
    write_db.commit()

    _audit(write_db, 0, "autopilot", "autopilot_run", f"自动运营({trigger})",
           f"扫描 {scanned}；新建 {len(created_ids)}；自动执行 {len(auto_ids)}；"
           f"跨Agent协调 {coord_summary}")
    write_db.commit()

    return {
        "success": True,
        "run_id": run.id,
        "trigger": trigger,
        "message": run.message,
        "scanned": scanned,
        "created_task_ids": created_ids,
        "auto_executed_task_ids": auto_ids,
        "pending_count": run.pending_count,
        "hints": hints,
        "kill_switch_on": killed,
        "coordination": coord_summary,
    }


def autopilot_runs(db, limit: int = 20) -> list[dict]:
    """自动运营历史（最近在前）"""
    rows = db.execute(
        select(AutopilotRun).order_by(AutopilotRun.id.desc()).limit(limit)
    ).scalars().all()
    out = []
    for r in rows:
        try:
            scanned = json.loads(r.scanned or "{}")
        except (json.JSONDecodeError, TypeError):
            scanned = {}
        try:
            exec_detail = json.loads(r.exec_detail or "[]")
        except (json.JSONDecodeError, TypeError):
            exec_detail = []
        out.append({
            "id": r.id,
            "trigger": r.trigger,
            "status": r.status,
            "message": r.message,
            "scanned": scanned,
            "created_count": r.created_count or 0,
            "auto_count": r.auto_count or 0,
            "pending_count": r.pending_count or 0,
            "hints_count": r.hints_count or 0,
            "coord_summary": (r.coord_summary or "")[:500],
            "exec_detail": exec_detail,
            "started_at": r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "",
            "finished_at": r.finished_at.strftime("%Y-%m-%d %H:%M:%S") if r.finished_at else "",
        })
    return out


def last_timer_run_at(db):
    """最近一次由定时器触发的运行时间（调度器据此判断是否到点）"""
    row = db.execute(
        select(AutopilotRun.started_at)
        .where(AutopilotRun.trigger == "timer")
        .order_by(AutopilotRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    return row


# ════════════════════════════════════════════════════════════════
# 十、建表
# ════════════════════════════════════════════════════════════════
def init_operation_tables() -> None:
    """创建运营执行层自管表（幂等；绝不触碰 ZT-agent 业务表）"""
    OwnBase.metadata.create_all(
        bind=write_engine,
        tables=[OperationTask.__table__, ActionAudit.__table__,
                AutonomySetting.__table__, AutopilotRun.__table__],
    )
    # L5 编排表（跨 Agent 协调事件日志）
    init_coordination_tables()
    with write_engine.begin() as conn:
        for ddl in (
            "ALTER TABLE operation_tasks ADD COLUMN auto_executed TINYINT(1) DEFAULT 0",
            "ALTER TABLE operation_tasks ADD COLUMN idem_key VARCHAR(120) DEFAULT ''",
            "ALTER TABLE operation_tasks ADD COLUMN exec_mode VARCHAR(10) DEFAULT ''",
            "ALTER TABLE autonomy_settings ADD COLUMN updated_at DATETIME NULL",
            "ALTER TABLE autopilot_runs ADD COLUMN coord_summary TEXT NULL",
            "ALTER TABLE autopilot_runs ADD COLUMN exec_detail TEXT NULL",
        ):
            try:
                conn.execute(_sql(ddl))
            except Exception:
                pass  # 列已存在等，忽略
