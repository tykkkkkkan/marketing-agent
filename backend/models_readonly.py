"""
ZT-agent 业务表的只读 ORM 映射
────────────────────────────────
字段与 ZT-agent `agent/models.py` 严格对齐（含 db_table 名、列名）。
本文件只用于 SELECT —— 配合 db.py 的只读引擎守卫，形成双重保险。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    DECIMAL,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """只读映射基类"""


class Products(Base):
    """商品（products）"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    sku = Column(String(32))
    spec = Column(String(50))
    target_fish = Column(String(50))
    retail_price = Column(DECIMAL(10, 2))
    wholesale_price = Column(DECIMAL(10, 2))
    description = Column(Text)
    is_active = Column(Boolean)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    inventory = relationship("Inventory", back_populates="product", uselist=False)


class Inventory(Base):
    """库存（inventory）—— 可用库存 = stock - reserved_stock"""
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    stock = Column(Integer, default=0)
    reserved_stock = Column(Integer, default=0)
    alert_line = Column(Integer, default=50)
    updated_at = Column(DateTime)

    product = relationship("Products", back_populates="inventory")

    @property
    def available_stock(self) -> int:
        return max(0, (self.stock or 0) - (self.reserved_stock or 0))


class Orders(Base):
    """订单（orders）—— 状态枚举：未发货/已发货/已取消/已退货"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    order_no = Column(String(50))
    customer_name = Column(String(50))
    phone = Column(String(20))
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name = Column(String(100))
    product_sku = Column(String(32))
    unit_price = Column(DECIMAL(10, 2))
    quantity = Column(Integer, default=1)
    total_price = Column(DECIMAL(10, 2))
    status = Column(String(20))
    ship_company = Column(String(50))
    tracking_no = Column(String(50))
    shipped_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    cancel_reason = Column(String(200))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class Wallet(Base):
    """公司钱包（company_wallet，单例 id=1）"""
    __tablename__ = "company_wallet"

    id = Column(Integer, primary_key=True)
    balance = Column(DECIMAL(14, 2), default=0)
    updated_at = Column(DateTime)


class Transaction(Base):
    """收支流水（wallet_transactions）"""
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey("company_wallet.id"))
    tx_type = Column(String(10))      # 收入 / 支出
    category = Column(String(20))     # 订单收入 / 订单退款 / 提现 / 手动调账 / 其他
    amount = Column(DECIMAL(14, 2))
    order_id = Column(Integer, ForeignKey("orders.id"))
    note = Column(String(200))
    created_at = Column(DateTime)


class KnowledgeChunk(Base):
    """企业知识库（knowledge_chunks）—— 第3步文案素材/合规校验复用"""
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True)
    title = Column(String(100))
    category = Column(String(50))
    keywords = Column(String(255))
    content = Column(Text)
    is_active = Column(Boolean)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class Conversations(Base):
    """对话记录（conversations）—— 用于挖掘高意向/高频咨询信号"""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64))
    role = Column(String(20))          # user / assistant
    content = Column(Text)
    created_at = Column(DateTime)
