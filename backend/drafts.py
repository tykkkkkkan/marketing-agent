"""
自管表 —— 营销文案草稿（本服务**唯一允许写入**的表）
───────────────────────────────────────────────────
它建在同一个 agent_db 内，但属于本服务新增的表，
严格遵守「只读 ZT-agent 业务表、写只走自己的新表」的边界约定。

营销闭环：AI 生成草稿 → 人工审核 → 通过才可发布（避免 AI 内容未经审核直接外发）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase

from db import write_engine


class OwnBase(DeclarativeBase):
    """自管表基类（与只读业务表彻底分离，避免误写）"""


class MarketingDraft(OwnBase):
    __tablename__ = "marketing_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), default="")
    channel = Column(String(50), default="朋友圈")
    tone = Column(String(50), default="促销")
    brief = Column(Text)
    content = Column(Text)
    # 状态机：待审核 → 已通过 / 已驳回
    status = Column(String(20), default="待审核", index=True)
    reviewer = Column(String(50), default="")
    review_note = Column(String(200), default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def init_own_tables() -> None:
    """创建自管表（只创建本服务的表，绝不触碰 ZT-agent 的业务表）"""
    OwnBase.metadata.create_all(bind=write_engine, tables=[MarketingDraft.__table__])
