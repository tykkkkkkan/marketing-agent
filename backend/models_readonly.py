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
    """订单（orders）

    状态枚举（**与 ZT-agent `OrderStatus` 严格对齐，6 种**）：
        未发货 / 已发货 / 已完成 / 退货申请中 / 已取消 / 已退货

    ⚠️ 本映射曾长期缺少 ZT 侧的个人中心与售后改造新增的 7 个字段
    （user_id / completed_at / return_*）。后果是营销侧对同一批订单的
    认知与 ZT 后台、C 端不一致：

      · 看不到「退货申请中」—— 用户提交退货申请到商家处理之间是**盲区**，
        退货率告警严重滞后（改造前只能靠 status='已退货' 反推）；
      · 无法区分「用户已确认收货」与「在途」，客户满意度/复购分析缺一环；
      · 无法按账号维度看订单归属，售后溯源只能靠手机号。
    字段缺失 = 两边口径必然漂移，所以这里按 ZT `agent/models.py` 全量补齐。
    """

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
    # 未发货 / 已发货 / 已完成 / 退货申请中 / 已取消 / 已退货
    status = Column(String(20))
    # ── 归属（C 端「我的订单」依据；None 表示登录体系上线前的历史订单）──
    user_id = Column(Integer)                      # 下单用户 id
    # ── 物流 ──
    ship_company = Column(String(50))
    tracking_no = Column(String(50))
    shipped_at = Column(DateTime)
    # ── 取消 ──
    cancelled_at = Column(DateTime)
    cancel_reason = Column(String(200))
    # ── 收货闭环 ──
    completed_at = Column(DateTime)                # 用户确认收货时间
    # ── 售后闭环（用户提交 → 商家处理）──
    return_status = Column(String(20))             # 待审核 / 已同意 / 已拒绝
    return_reason = Column(String(200))            # 用户填写的退货原因
    return_requested_at = Column(DateTime)         # 用户申请时间
    return_handled_at = Column(DateTime)           # 商家处理时间
    return_note = Column(String(200))              # 商家处理备注 / 拒绝理由
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    # ── 派生属性（供分析层复用，避免各处自己拼 SQL）────────────────
    @property
    def is_open(self) -> bool:
        """是否为进行中订单（未取消、未退货）"""
        return self.status not in ("已取消", "已退货")

    @property
    def has_return_request(self) -> bool:
        """是否有退货申请（含审核中），无需等待商家处理即可观测"""
        return bool(self.return_status) or self.status == "退货申请中"


class Wallet(Base):
    """公司钱包（company_wallet，单例 id=1）"""
    __tablename__ = "company_wallet"

    id = Column(Integer, primary_key=True)
    balance = Column(DECIMAL(14, 2), default=0)
    updated_at = Column(DateTime)


class Transaction(Base):
    """收支流水（wallet_transactions）

    `operator_id` 是 ZT 侧记录「谁做的这次调账」的字段（手动充值/扣款写操作人，
    系统自动记账为 NULL）。缺了它，营销侧看流水时分不清哪笔是人工改的，
    对账时会把人工调账误判成订单收入。
    """

    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True)
    wallet_id = Column(Integer, ForeignKey("company_wallet.id"))
    tx_type = Column(String(10))      # 收入 / 支出
    category = Column(String(20))     # 订单收入 / 订单退款 / 提现 / 手动调账 / 其他
    amount = Column(DECIMAL(14, 2))
    order_id = Column(Integer, ForeignKey("orders.id"))
    note = Column(String(200))
    operator_id = Column(Integer)     # 操作人 id；NULL 表示系统自动记账
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
