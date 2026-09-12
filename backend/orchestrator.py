"""
L5 多智能体编排层（Orchestrator）—— 让两个 Agent 真正「协作」
════════════════════════════════════════════════════════════════════
营销 Agent（marketing-agent）在这里扮演「编排者 / 协调中枢」角色：

  · 监听 ZT-agent 的 C 端信号（只读 agent_db，零侵入）：
      - 断货：某商品可用库存 ≤ 0；
      - 退货率飙升：某商品退货率超过阈值。
  · 向 ZT-agent 发出协调指令（经其接收钩子 POST /api/coordination/inbound/）：
      - 断货 → 请求前台「暂停购买 + 挂客户提示」，避免客户下单买不到的东西；
  · 接收 ZT-agent 主动发来的事件（营销侧入站 POST /api/coordination/inbound/）：
      - 退货申请 → 生成营销侧「复盘该商品售后策略」任务。

设计要点（与既有架构一致）：
  - 全程不直写 ZT 业务表；协调动作只走既有接口 / 接收钩子；
  - 有界：ZT 侧只接受白名单事件（pause_product/resume_product/set_notice）；
  - 可审计：每一次协调都落 coordination_events 表；
  - 可被急停 / 策略收敛：coord_enabled 关掉即整体停用，coord_auto_apply
    关掉则「只建议、不自动发」，转人工在面板里点确认。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text, case, func, select

from db import read_engine, write_engine
from drafts import OwnBase
from zt_client import client as zt_client

# 协调事件种类
KIND_STOCKOUT_PAUSE = "stockout_pause"     # 断货 → 请求前台暂停购买
KIND_RETURN_SURGE = "return_surge"         # 退货率飙升 → 营销侧复盘
KIND_INBOUND_RETURN = "inbound_return"     # ZT 主动告知：有退货申请

# 协调方向
DIR_MARKETING_TO_ZT = "marketing→zt"
DIR_ZT_TO_MARKETING = "zt→marketing"
DIR_INTERNAL = "internal"

# 协调状态
CST_APPLIED = "applied"        # 已自动发往 ZT 并生效
CST_PENDING = "pending"       # 待人工确认（coord_auto_apply=off 时）
CST_RECEIVED = "received"     # 收到 ZT 事件，已登记
CST_SKIPPED = "skipped"       # 本次未触发 / 已处理过
CST_RESOLVED = "resolved"     # 人工已处理

# 退货率判定最小样本（避免 1 单退货就被误判飙升）
MIN_RETURN_SAMPLE = 3


class CoordinationEvent(OwnBase):
    """跨 Agent 协调事件日志（营销 Agent 自管表，绝不触碰 ZT 业务表）"""
    __tablename__ = "coordination_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(30), index=True, default="")
    direction = Column(String(20), default=DIR_INTERNAL)
    product_id = Column(Integer, default=0)
    product_name = Column(String(100), default="")
    sku = Column(String(32), default="")
    status = Column(String(20), default=CST_PENDING, index=True)
    detail = Column(Text, default="")                         # 人话描述
    payload = Column(Text, default="{}")                     # 原始数据（JSON）
    source = Column(String(30), default="")
    created_at = Column(DateTime, default=datetime.now)
    resolved_at = Column(DateTime, nullable=True)


# ════════════════════════════════════════════════════════════════
# 一、向 ZT-agent 发协调指令（只走其接收钩子）
# ════════════════════════════════════════════════════════════════
def emit_pause_product(product_id: int, product_name: str, dry_run: bool = False) -> dict:
    """请求 ZT-agent 前台暂停该商品购买并挂客户提示。

    返回 zt_client.coordinate() 的统一结构（含 dry_run 回显）。
    """
    notice = f"「{product_name}」暂时缺货，可先收藏或咨询客服，恢复库存后即可下单。"
    return zt_client.coordinate(
        event="pause_product", product_id=product_id, notice=notice, source="marketing-agent",
    )


# ════════════════════════════════════════════════════════════════
# 二、协调巡检：从只读数据里发现需要跨 Agent 协同的信号
# ════════════════════════════════════════════════════════════════
def _active_pause_product_ids(write_db) -> set[int]:
    """已经登记过「暂停购买」协调且未解决的商品，避免重复发。"""
    rows = write_db.execute(
        select(CoordinationEvent.product_id).where(
            CoordinationEvent.kind == KIND_STOCKOUT_PAUSE,
            CoordinationEvent.status.in_([CST_APPLIED, CST_PENDING]),
        )
    ).all()
    return {r[0] for r in rows}


def _handled_return_product_ids(write_db) -> set[int]:
    """已经处理过退货飙升复盘的商品，避免重复建。"""
    rows = write_db.execute(
        select(CoordinationEvent.product_id).where(
            CoordinationEvent.kind == KIND_RETURN_SURGE,
            CoordinationEvent.status.in_([CST_PENDING, CST_RESOLVED]),
        )
    ).all()
    return {r[0] for r in rows}


def _out_of_stock_products(read_db) -> list[dict]:
    from models_readonly import Inventory, Products

    out = []
    rows = read_db.execute(
        select(Inventory, Products)
        .join(Products, Products.id == Inventory.product_id)
        .where(Products.is_active.is_(True))
    ).all()
    for inv, prod in rows:
        stock = int(inv.stock or 0)
        reserved = int(inv.reserved_stock or 0)
        available = max(0, stock - reserved)
        if available <= 0:
            out.append({
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.sku or "",
                "available": available,
                "stock": stock,
            })
    return out


def _return_surge_products(read_db, threshold_pct: float) -> list[dict]:
    """按商品统计退货率，返回超过阈值的（且样本数足够）。

    ⚠️ 两处口径修正（原实现会漏报，属"数据不同步"）：

      1. **分母**：原来用「该商品的全部订单」，但退货只可能来自**发过货**的订单，
         待发货 / 已取消的单永远不可能退货，算进分母会把退货率稀释掉。
         现在分母 = 状态 ∈ {已发货, 已完成, 退货申请中, 已退货}。

      2. **分子**：原来只数 `status == '已退货'`（商家已同意）。但用户提交退货申请后
         状态先变成「退货申请中」，**商家没点同意之前完全统计不到** ——
         而这段时间恰恰是最该干预的窗口（货还没退回来，还有挽回余地）。
         现在分子 = 「退货申请中」+「已退货」，并在返回里分列 `returning` / `returned`，
         让运营一眼看出「已退几单、还有几单在申请中」。
    """
    from models_readonly import Orders

    EVER_SHIPPED = ("已发货", "已完成", "退货申请中", "已退货")
    rows = read_db.execute(
        select(
            Orders.product_id,
            Orders.product_name,
            Orders.product_sku,
            func.count().label("total"),
            func.sum(case((Orders.status == "已退货", 1), else_=0)).label("returned"),
            func.sum(case((Orders.status == "退货申请中", 1), else_=0)).label("returning"),
        )
        .where(Orders.status.in_(EVER_SHIPPED))
        .group_by(Orders.product_id, Orders.product_name, Orders.product_sku)
    ).all()

    out = []
    for pid, name, sku, total, returned, returning in rows:
        total = int(total or 0)
        returned = int(returned or 0)
        returning = int(returning or 0)
        # 退货申请中尚未退款，但从「客户体验恶化」角度已该关注，一并计入告警分子
        risky = returned + returning
        if total < MIN_RETURN_SAMPLE:
            continue
        rate = (risky / total) * 100 if total else 0
        if rate >= threshold_pct:
            out.append({
                "product_id": pid,
                "product_name": name,
                "sku": sku or "",
                "total": total,
                "returned": returned,
                "returning": returning,
                "rate": round(rate, 1),
            })
    return out


# ════════════════════════════════════════════════════════════════
# 三、跑一轮协调（被自动运营调度器与手动按钮调用）
# ════════════════════════════════════════════════════════════════
def run_coordination_scan(write_db, read_db, policy: dict, actor: str = "自动运营") -> dict:
    """巡检 → 生成协调事件（是否自动发往 ZT 由策略决定）。

    返回结构化摘要，便于汇进自动运营报告。
    """
    enabled = (policy.get("coord_enabled") or "off") == "on"
    auto_apply = (policy.get("coord_auto_apply") or "off") == "on"
    dry_run = (policy.get("execute_mode") or "live") == "dry_run"
    try:
        threshold = float(policy.get("coord_return_surge_threshold") or 20)
    except (TypeError, ValueError):
        threshold = 20

    summary = {
        "enabled": enabled,
        "auto_apply": auto_apply,
        "stockout_emitted": [],
        "stockout_pending": [],
        "return_surge_flagged": [],
        "skipped": 0,
    }
    if not enabled:
        summary["skipped"] = 1
        summary["text"] = "跨Agent协调未开启，本轮没有检查断货和退货。"
        return summary

    emitted_names: list = []
    pending_names: list = []
    return_names: list = []

    # ── Flow A：断货 → 请求前台暂停购买 ──
    paused = _active_pause_product_ids(write_db)
    for p in _out_of_stock_products(read_db):
        pid = p["product_id"]
        if pid in paused:
            continue
        detail = f"「{p['product_name']}」可用库存为 {p['available']}，已售罄；请求前台暂停购买并挂客户提示。"
        evt = CoordinationEvent(
            kind=KIND_STOCKOUT_PAUSE, direction=DIR_MARKETING_TO_ZT,
            product_id=pid, product_name=p["product_name"], sku=p["sku"],
            detail=detail, payload=json.dumps(p, ensure_ascii=False), source=actor,
        )
        if auto_apply and not dry_run:
            res = emit_pause_product(pid, p["product_name"], dry_run=False)
            if res.get("ok"):
                evt.status = CST_APPLIED
                evt.detail = detail + "（已自动发往 ZT-agent 生效）"
                summary["stockout_emitted"].append(pid)
                emitted_names.append(p["product_name"])
            else:
                evt.status = CST_PENDING
                evt.detail = detail + f"（发送失败：{res.get('message','')}；转人工）"
                summary["stockout_pending"].append(pid)
                pending_names.append(p["product_name"])
        else:
            evt.status = CST_PENDING
            summary["stockout_pending"].append(pid)
            pending_names.append(p["product_name"])
        write_db.add(evt)
    write_db.commit()

    # ── Flow B：退货率飙升 → 营销侧生成复盘任务 ──
    handled = _handled_return_product_ids(write_db)
    for p in _return_surge_products(read_db, threshold):
        pid = p["product_id"]
        if pid in handled:
            continue
        detail = (
            f"「{p['product_name']}」曾发货 {p['total']} 单，其中已退货 {p['returned']} 单、"
            f"退货申请中 {p['returning']} 单，退货率 {p['rate']}% ≥ 阈值 {threshold}%；"
            f"建议复盘该商品的质量/描述/售后策略"
            + ("（有申请待处理，货未退回，可优先挽回）" if p.get("returning") else "。")
        )
        evt = CoordinationEvent(
            kind=KIND_RETURN_SURGE, direction=DIR_INTERNAL,
            product_id=pid, product_name=p["product_name"], sku=p["sku"],
            detail=detail, payload=json.dumps(p, ensure_ascii=False),
            status=CST_PENDING, source=actor,
        )
        write_db.add(evt)
        summary["return_surge_flagged"].append(pid)
        return_names.append(p["product_name"])
    write_db.commit()

    # 人话版摘要（审计与报告直接引用，不再甩 JSON）
    parts = []
    if emitted_names:
        parts.append("发现 " + "、".join(emitted_names) + " 已缺货，已自动通知商城前台暂停购买并挂出客户提示")
    if pending_names:
        parts.append("、".join(pending_names) + " 已缺货，暂停购买申请已登记，等人工确认后才会生效")
    if return_names:
        parts.append("、".join(return_names) + " 退货率偏高，已生成复盘提醒")
    if parts:
        summary["text"] = "跨Agent协调：" + "；".join(parts) + "。"
    else:
        summary["text"] = "跨Agent协调已开启，本轮没有发现断货或退货异常。"
    return summary


# ════════════════════════════════════════════════════════════════
# 四、接收 ZT-agent 主动发来的事件
# ════════════════════════════════════════════════════════════════
def handle_inbound_from_zt(write_db, event: str, payload: dict) -> dict:
    """处理 ZT-agent 发来的协调事件（如退货申请）。

    返回统一结构：{ok, message, data}。
    """
    if event == "return_requested":
        pid = int(payload.get("product_id") or 0)
        name = payload.get("product_name") or f"商品#{pid}"
        detail = payload.get("detail") or f"「{name}」收到一笔退货申请，建议营销侧关注其售后体验。"
        evt = CoordinationEvent(
            kind=KIND_INBOUND_RETURN, direction=DIR_ZT_TO_MARKETING,
            product_id=pid, product_name=name, sku=payload.get("sku") or "",
            detail=detail, payload=json.dumps(payload, ensure_ascii=False),
            status=CST_RECEIVED, source="ZT-agent",
        )
        write_db.add(evt)
        write_db.commit()
        return {"ok": True, "message": "已登记 ZT-agent 退货事件", "data": {"event_id": evt.id}}
    return {"ok": False, "message": f"不支持的事件：{event}"}


# ════════════════════════════════════════════════════════════════
# 五、人工确认一条「待确认」的协调事件（面板上的确认按钮）
# ════════════════════════════════════════════════════════════════
def apply_pending_event(write_db, event_id: int, actor: str = "运营") -> dict:
    evt = write_db.get(CoordinationEvent, int(event_id))
    if not evt:
        return {"ok": False, "message": "事件不存在"}
    if evt.status != CST_PENDING:
        return {"ok": False, "message": f"事件状态为 {evt.status}，无需处理"}
    if evt.kind == KIND_STOCKOUT_PAUSE:
        res = emit_pause_product(evt.product_id, evt.product_name, dry_run=False)
        if res.get("ok"):
            evt.status = CST_APPLIED
            evt.detail = evt.detail.split("（")[0] + "（已发往 ZT-agent 生效）"
        else:
            return {"ok": False, "message": f"发送失败：{res.get('message','')}"}
    else:
        evt.status = CST_RESOLVED
    evt.resolved_at = datetime.now()
    write_db.commit()
    return {"ok": True, "message": "已处理", "data": {"event_id": evt.id, "status": evt.status}}


# ════════════════════════════════════════════════════════════════
# 六、列表
# ════════════════════════════════════════════════════════════════
def coordination_events_list(write_db, limit: int = 50) -> list[dict]:
    rows = write_db.execute(
        select(CoordinationEvent).order_by(CoordinationEvent.id.desc()).limit(limit)
    ).scalars().all()
    out = []
    for e in rows:
        try:
            payload = json.loads(e.payload or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        out.append({
            "id": e.id,
            "kind": e.kind,
            "direction": e.direction,
            "product_id": e.product_id,
            "product_name": e.product_name,
            "sku": e.sku,
            "status": e.status,
            "detail": e.detail,
            "payload": payload,
            "source": e.source,
            "created_at": e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "",
            "resolved_at": e.resolved_at.strftime("%Y-%m-%d %H:%M:%S") if e.resolved_at else "",
        })
    return out


def init_coordination_tables() -> None:
    """创建协调事件表（幂等）"""
    OwnBase.metadata.create_all(bind=write_engine, tables=[CoordinationEvent.__table__])
