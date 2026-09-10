"""
营销工具集 —— 供 LangChain Agent 调用
──────────────────────────────────────
设计要点：每个工具都是一次「只读」数据访问，Agent 靠工具拿事实，
从根本上抑制大模型编造销量/价格/库存数字（幻觉）。

共 4 个工具：
  1. get_sales_ranking            查销量排行 → 定主推商品
  2. get_low_stock_products       查库存预警 → 避开缺货品
  3. search_knowledge             检索企业知识库 → 取卖点素材
  4. get_recent_customer_questions 看客户近期咨询 → 找营销切入点
"""
from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.tools import tool
from sqlalchemy import func, or_, select

from db import ReadSession
from models_readonly import Conversations, Inventory, KnowledgeChunk, Orders, Products


def _f(v) -> float:
    return float(v or 0)


@tool
def get_sales_ranking(limit: int = 5, days: int = 90) -> str:
    """查询最近若干天的商品销量排行，返回销量最高的商品及销售额。
    用于确定营销主推商品。参数：limit=返回条数（默认5），days=统计最近多少天（默认90）。"""
    since = datetime.now() - timedelta(days=days)
    with ReadSession() as db:
        stmt = (
            select(
                Orders.product_name,
                func.coalesce(func.sum(Orders.quantity), 0).label("qty"),
                func.coalesce(func.sum(Orders.total_price), 0).label("rev"),
            )
            .where(Orders.status != "已取消", Orders.created_at >= since)
            .group_by(Orders.product_name)
            .order_by(func.coalesce(func.sum(Orders.quantity), 0).desc())
            .limit(limit)
        )
        rows = db.execute(stmt).all()
    if not rows:
        return "最近无销售数据。"
    return "\n".join(
        f"{i + 1}. {r.product_name or '未知商品'} —— 销量 {int(r.qty)} 件，销售额 ¥{_f(r.rev):.2f}"
        for i, r in enumerate(rows)
    )


@tool
def get_low_stock_products(limit: int = 10) -> str:
    """查询可用库存低于预警线的商品。这些商品库存紧张，不宜作为促销主推（会加剧缺货）。
    参数：limit=返回条数（默认10）。"""
    with ReadSession() as db:
        stmt = (
            select(
                Products.name,
                Inventory.stock,
                Inventory.reserved_stock,
                Inventory.alert_line,
            )
            .join(Inventory, Inventory.product_id == Products.id)
            .where(
                (Inventory.stock - Inventory.reserved_stock) <= Inventory.alert_line,
                Products.is_active.is_(True),
            )
            .order_by((Inventory.stock - Inventory.reserved_stock).asc())
            .limit(limit)
        )
        rows = db.execute(stmt).all()
    if not rows:
        return "当前无库存预警商品。"
    return "\n".join(
        f"{r.name}: 可用 {max(0, (r.stock or 0) - (r.reserved_stock or 0))} 件"
        f"（预警线 {r.alert_line}）"
        for r in rows
    )


@tool
def search_knowledge(keyword: str) -> str:
    """检索企业知识库，获取产品卖点、钓法知识、企业政策等素材。
    参数：keyword=检索关键词，如「蓝鲫」「春季钓鲫鱼」「退换货政策」。"""
    with ReadSession() as db:
        like = f"%{keyword}%"
        stmt = (
            select(KnowledgeChunk.title, KnowledgeChunk.content)
            .where(KnowledgeChunk.is_active.is_(True))
            .where(
                or_(
                    KnowledgeChunk.title.like(like),
                    KnowledgeChunk.keywords.like(like),
                    KnowledgeChunk.content.like(like),
                )
            )
            .limit(3)
        )
        rows = db.execute(stmt).all()
    if not rows:
        return f"知识库中未找到与「{keyword}」相关的内容。"
    return "\n\n".join(f"【{r.title}】{(r.content or '')[:300]}" for r in rows)


@tool
def get_recent_customer_questions(limit: int = 10) -> str:
    """获取客户最近在客服对话中提出的问题，用于挖掘客户关注点与营销切入点。
    参数：limit=返回条数（默认10）。"""
    with ReadSession() as db:
        stmt = (
            select(Conversations.content)
            .where(Conversations.role == "user")
            .order_by(Conversations.created_at.desc())
            .limit(limit)
        )
        rows = db.execute(stmt).all()
    if not rows:
        return "暂无客户咨询记录。"
    return "\n".join(f"- {(r.content or '')[:80]}" for r in rows)


# 工具清单（供 Agent 绑定）
MARKETING_TOOLS = [
    get_sales_ranking,
    get_low_stock_products,
    search_knowledge,
    get_recent_customer_questions,
]
