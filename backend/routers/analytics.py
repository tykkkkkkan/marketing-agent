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

CANCELLED = "已取消"
PENDING_SHIP = "未发货"


def _f(value) -> float:
    """Decimal → float，前端可直接使用"""
    return float(value or 0)


def _db_unavailable() -> HTTPException:
    """统一 503，不向外泄漏数据库内部错误细节"""
    return HTTPException(status_code=503, detail="数据库暂时不可用，请稍后重试")


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
    """订单总数 / 有效订单 / GMV / 钱包余额 / 待发货 / 库存预警数"""
    try:
        total_orders = db.scalar(select(func.count(Orders.id))) or 0
        valid = Orders.status != CANCELLED
        valid_orders = db.scalar(select(func.count(Orders.id)).where(valid)) or 0
        gmv = db.scalar(select(func.coalesce(func.sum(Orders.total_price), 0)).where(valid))
        pending_ship = db.scalar(
            select(func.count(Orders.id)).where(Orders.status == PENDING_SHIP)
        ) or 0
        low_stock_count = db.scalar(
            select(func.count(Inventory.id)).where(
                (Inventory.stock - Inventory.reserved_stock) <= Inventory.alert_line
            )
        ) or 0
        wallet = db.get(Wallet, 1)
        balance = wallet.balance if wallet else Decimal("0")

        return {
            "success": True,
            "message": "ok",
            "data": {
                "total_orders": int(total_orders),
                "valid_orders": int(valid_orders),
                "gmv": _f(gmv),
                "wallet_balance": _f(balance),
                "pending_ship": int(pending_ship),
                "low_stock_count": int(low_stock_count),
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
    """按销量倒序的商品排行（含销售额与订单数）"""
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
            .where(Orders.status != CANCELLED, Orders.created_at >= since)
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
