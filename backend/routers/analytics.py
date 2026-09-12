"""
经营分析接口 —— 只读消费 ZT-agent 的订单 / 库存 / 钱包数据
────────────────────────────────────────────────────────────
数据全部来自 ZT-agent 的 agent_db，本服务**不写任何业务表**。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from db import get_read_session
from models_readonly import Inventory, Orders, Products, Wallet

router = APIRouter(prefix="/api/analytics", tags=["经营分析"])

# ════════════════════════════════════════════════════════════════════
# 订单状态口径（**必须与 ZT-agent 严格一致，否则两套系统会对同一批订单
# 报出不同的金额与单数 —— 这正是"数据不同步"的主要来源**）
# ════════════════════════════════════════════════════════════════════
# ZT 侧 agent/models.py 的 OrderStatus 共 6 态：
PENDING_SHIP = "未发货"          # 已下单，尚未发货（无收入）
SHIPPED = "已发货"               # 已发货（ZT 此时记「订单收入」）
COMPLETED = "已完成"             # 用户确认收货
RETURNING = "退货申请中"          # 用户已申请退货，等商家处理
CANCELLED = "已取消"             # 未发货时取消（不产生收入）
RETURNED = "已退货"              # 商家同意退货（ZT 记「订单退款」+ 库存回滚）

# 「已成交」= 真正产生过/正在产生收入的订单，与 ZT 个人中心
# `_order_stats.total_amount` 的口径**完全一致**（已发货 + 已完成 + 退货申请中）。
# 改造前这里写的是 `status != "已取消"`，把「已退货」也算成成交 ——
# GMV 虚高、客单价偏高，与 ZT 后台对不上账。
PAID_STATUSES = (SHIPPED, COMPLETED, RETURNING)
# 「已终结且不计收入」
CLOSED_STATUSES = (CANCELLED, RETURNED)
# 「曾发货」= 可能发生退货的集合（退货只能来源于发过货的订单）
EVER_SHIPPED_STATUSES = (SHIPPED, COMPLETED, RETURNING, RETURNED)


def _f(value) -> float:
    """Decimal → float，前端可直接使用"""
    return float(value or 0)


def _db_unavailable() -> HTTPException:
    """统一 503，不向外泄漏数据库内部错误细节"""
    return HTTPException(status_code=503, detail="数据库暂时不可用，请稍后重试")


def collect_metrics(db: Session) -> dict:
    """聚合经营指标（**唯一实现**：overview 与 diagnosis 共用，杜绝两处口径漂移）。

    口径说明（每条都对齐 ZT-agent）：
      · total_orders     全部订单
      · paid_orders      成交订单 = 已发货 + 已完成 + 退货申请中（= ZT total_amount 口径）
      · pending_orders   待发货（尚未成交）
      · returning_orders 退货申请中（用户已申请、商家未处理 —— 改造前完全看不到）
      · refunded_orders  已退货
      · gmv              成交额 = Σ total_price(成交订单)
      · avg_order_value  客单价 = gmv / paid_orders
      · refund_rate      退货率 = 已退货 / 曾发货订单（分母用"曾发货"，因为退货只可能来自发过货的单）
      · return_request_rate 退货申请率 = (退货申请中 + 已退货) / 曾发货订单（**提前预警**用）
      · healthy_ratio    库存健康度 = (在售商品 - 库存预警数) / 在售商品
    """
    total_orders = db.scalar(select(func.count(Orders.id))) or 0

    def _count(statuses):
        return db.scalar(
            select(func.count(Orders.id)).where(Orders.status.in_(list(statuses)))
        ) or 0

    paid_orders = _count(PAID_STATUSES)
    pending_orders = _count([PENDING_SHIP])
    returning_orders = _count([RETURNING])
    refunded_orders = _count([RETURNED])
    cancelled_orders = _count([CANCELLED])
    ever_shipped = _count(EVER_SHIPPED_STATUSES)

    gmv = _f(db.scalar(
        select(func.coalesce(func.sum(Orders.total_price), 0))
        .where(Orders.status.in_(list(PAID_STATUSES)))
    ))

    low_stock_count = db.scalar(
        select(func.count(Inventory.id)).where(
            (Inventory.stock - Inventory.reserved_stock) <= Inventory.alert_line
        )
    ) or 0
    active_products = db.scalar(
        select(func.count(Products.id)).where(Products.is_active.is_(True))
    ) or 0
    healthy_ratio = (
        round((active_products - low_stock_count) / active_products * 100, 1)
        if active_products else 0
    )

    # 退货率（以"曾发货订单"为分母）+ 退货申请率（含审核中，用于提前预警）
    refund_rate = round(refunded_orders / ever_shipped * 100, 1) if ever_shipped else 0
    return_request_rate = (
        round((returning_orders + refunded_orders) / ever_shipped * 100, 1)
        if ever_shipped else 0
    )

    # 客户与复购（只统计成交订单，未成交的咨询单不应算作"买过"）
    cust_rows = db.execute(
        select(Orders.customer_name, func.count(Orders.id).label("cnt"))
        .where(
            Orders.customer_name.isnot(None), Orders.customer_name != "",
            Orders.status.in_(list(PAID_STATUSES)),
        )
        .group_by(Orders.customer_name)
    ).all()
    customer_count = len(cust_rows)
    repeat_customers = sum(1 for r in cust_rows if r.cnt >= 2)
    repeat_rate = round(repeat_customers / customer_count * 100, 1) if customer_count else 0

    wallet = db.get(Wallet, 1)
    balance = wallet.balance if wallet else Decimal("0")

    return {
        "total_orders": int(total_orders),
        # 兼容旧字段名（前端看板在用）：valid_orders 现在等价于「成交订单」
        "valid_orders": int(paid_orders),
        "paid_orders": int(paid_orders),
        "pending_orders": int(pending_orders),
        "returning_orders": int(returning_orders),
        "refunded_orders": int(refunded_orders),
        "cancelled_orders": int(cancelled_orders),
        "gmv": gmv,
        "avg_order_value": round(gmv / paid_orders, 2) if paid_orders else 0,
        "pending_ship": int(pending_orders),     # 兼容旧字段名
        "low_stock_count": int(low_stock_count),
        "healthy_ratio": healthy_ratio,
        "refund_rate": refund_rate,
        "return_request_rate": return_request_rate,
        "customer_count": int(customer_count),
        "repeat_rate": repeat_rate,
        "wallet_balance": _f(balance),
    }


@router.get("/health-db", summary="数据库连通性自检")
def health_db(db: Session = Depends(get_read_session)):
    """验证与 ZT-agent 的 agent_db 是否真的连通"""
    try:
        n = db.scalar(select(func.count(Orders.id)))
        return {
            "success": True,
            "message": "agent_db 连接正常",
            "data": {"database": "agent_db", "orders": int(n or 0)},
        }
    except SQLAlchemyError:
        raise _db_unavailable()


@router.get("/overview", summary="经营概览")
def overview(db: Session = Depends(get_read_session)):
    """订单总数 / 成交订单 / GMV / 钱包余额 / 待发货 / 退货 / 库存预警数

    与 /diagnosis 共用 collect_metrics —— 两个接口对同一批数据必须给出同一组数字，
    改造前它们各自写了一份统计 SQL，口径不一致时前端两个页面会互相矛盾。
    """
    try:
        m = collect_metrics(db)
        return {
            "success": True,
            "message": "ok",
            "data": {
                "total_orders": m["total_orders"],
                "valid_orders": m["valid_orders"],
                "paid_orders": m["paid_orders"],
                "gmv": m["gmv"],
                "wallet_balance": m["wallet_balance"],
                "pending_ship": m["pending_ship"],
                "pending_orders": m["pending_orders"],
                "returning_orders": m["returning_orders"],
                "refunded_orders": m["refunded_orders"],
                "refund_rate": m["refund_rate"],
                "return_request_rate": m["return_request_rate"],
                "low_stock_count": m["low_stock_count"],
            },
        }
    except SQLAlchemyError:
        raise _db_unavailable()


@router.get("/sales-top", summary="销量排行 Top N")
def sales_top(
    limit: int = Query(5, ge=1, le=50, description="返回条数"),
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天"),
    db: Session = Depends(get_read_session),
):
    """按销量倒序的商品排行（含销售额与订单数）

    只统计**成交订单**（已发货 + 已完成 + 退货申请中），与 GMV 口径一致 ——
    改造前用的是 `status != '已取消'`，把「已退货」也计入销量与销售额，
    导致排行榜与 GMV 对不上（同一个人在两个卡片里看到不同的数）。
    """
    since = datetime.now() - timedelta(days=days)
    try:
        stmt = (
            select(
                Orders.product_name.label("product_name"),
                Orders.product_sku.label("product_sku"),
                func.coalesce(func.sum(Orders.quantity), 0).label("sold_qty"),
                func.coalesce(func.sum(Orders.total_price), 0).label("revenue"),
                func.count(Orders.id).label("order_count"),
            )
            .where(Orders.status.in_(list(PAID_STATUSES)), Orders.created_at >= since)
            .group_by(Orders.product_name, Orders.product_sku)
            .order_by(func.coalesce(func.sum(Orders.quantity), 0).desc())
            .limit(limit)
        )
        rows = db.execute(stmt).all()
        data = [
            {
                "product_name": r.product_name or "未知商品",
                "product_sku": r.product_sku or "",
                "sold_qty": int(r.sold_qty),
                "revenue": _f(r.revenue),
                "order_count": int(r.order_count),
            }
            for r in rows
        ]
        return {"success": True, "message": "ok", "data": data}
    except SQLAlchemyError:
        raise _db_unavailable()


@router.get("/low-stock", summary="库存预警")
def low_stock(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_read_session),
):
    """可用库存（stock - reserved_stock）低于预警线的在售商品"""
    try:
        stmt = (
            select(Inventory, Products)
            .join(Products, Inventory.product_id == Products.id)
            .where(
                (Inventory.stock - Inventory.reserved_stock) <= Inventory.alert_line,
                Products.is_active.is_(True),
            )
            .order_by((Inventory.stock - Inventory.reserved_stock).asc())
            .limit(limit)
        )
        rows = db.execute(stmt).all()
        data = [
            {
                "product_name": p.name,
                "product_sku": p.sku or "",
                "stock": int(inv.stock or 0),
                "reserved_stock": int(inv.reserved_stock or 0),
                "available_stock": max(0, int(inv.stock or 0) - int(inv.reserved_stock or 0)),
                "alert_line": int(inv.alert_line or 0),
            }
            for inv, p in rows
        ]
        return {"success": True, "message": "ok", "data": data}
    except SQLAlchemyError:
        raise _db_unavailable()


@router.get("/hot-questions", summary="咨询热点挖掘")
def hot_questions(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_read_session),
):
    """从 ZT-agent 的 conversations 中取最近用户提问，供营销侧挖掘选品与卖点。

    只读、只取最近 N 条用户消息（role='user'），不做任何写入。
    """
    try:
        from models_readonly import Conversations

        stmt = (
            select(Conversations.content, Conversations.created_at)
            .where(Conversations.role == "user")
            .order_by(Conversations.created_at.desc())
            .limit(limit)
        )
        rows = db.execute(stmt).all()
        data = [
            {
                "content": (r.content or "")[:200],
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            }
            for r in rows
        ]
        return {"success": True, "message": "ok", "data": data}
    except SQLAlchemyError:
        raise _db_unavailable()


@router.get("/diagnosis", summary="经营诊断（真实指标 + AI 行动建议）")
def diagnosis(db: Session = Depends(get_read_session)):
    """聚合真实经营指标，并用大模型生成面向企业主的「说人话」行动建议。

    指标全部来自 ZT-agent 业务表（只读），且与 /overview 共用 `collect_metrics`
    —— 同一批数据在「经营概览」与「经营诊断」里不会出现两个数。
    AI 建议基于这些事实生成，不编造数据；大模型不可用时 insight 为 None，
    但 metrics 仍正常返回。
    """
    try:
        metrics = collect_metrics(db)
    except SQLAlchemyError:
        raise _db_unavailable()

    # AI 行动建议（基于上述事实，失败不影响指标返回）
    insight = None
    try:
        from langchain_openai import ChatOpenAI

        from marketing_agent import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

        if DEEPSEEK_API_KEY:
            prompt = (
                "你是渔具企业（中渔天下）的经营顾问，请用口语化、老板能听懂的话，"
                "基于以下真实经营指标，给出 3 条可落地的行动建议。"
                "每条建议先说结论，再说理由，不要编造数字。\n\n"
                f"成交订单 {metrics['paid_orders']} 单（另有 {metrics['pending_orders']} 单待发货），"
                f"成交额 ¥{metrics['gmv']:.0f}，客单价 ¥{metrics['avg_order_value']:.0f}；\n"
                f"退货情况：已退 {metrics['refunded_orders']} 单（退货率 {metrics['refund_rate']}%），"
                f"另有 {metrics['returning_orders']} 单退货申请待处理"
                f"（退货申请率 {metrics['return_request_rate']}%）；\n"
                f"库存预警商品 {metrics['low_stock_count']} 项，库存健康度 {metrics['healthy_ratio']}%；\n"
                f"成交客户 {metrics['customer_count']} 人，复购率 {metrics['repeat_rate']}%。\n\n"
                "请用如下格式输出（不要其它废话）：\n"
                "1. 结论：xxx｜理由：xxx\n2. 结论：xxx｜理由：xxx\n3. 结论：xxx｜理由：xxx"
            )
            llm = ChatOpenAI(
                model=DEEPSEEK_MODEL,
                base_url=DEEPSEEK_BASE_URL,
                api_key=DEEPSEEK_API_KEY,
                temperature=0.5,
                timeout=60,
                max_retries=1,
            )
            resp = llm.invoke(prompt)
            insight = (getattr(resp, "content", "") or "").strip()
    except Exception:
        insight = None

    return {
        "success": True,
        "message": "ok",
        "data": {
            "metrics": metrics,
            "insight": insight,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    }
