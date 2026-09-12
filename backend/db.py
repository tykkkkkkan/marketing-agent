"""
数据库连接层 —— 只读消费 ZT-agent 的 agent_db
──────────────────────────────────────────────
两条引擎，职责分离（把「只读」做成硬约束，而非口头约定）：

  · read_engine  ：查询 ZT-agent 的业务表（products/orders/inventory/...），
                   挂 before_cursor_execute 守卫，任何写操作直接抛 PermissionError。
  · write_engine ：仅供本服务自管表（marketing_drafts）使用，绝不触碰业务表。

配置来源：优先本服务 .env，缺失时回落到 ZT-agent/.env（只读复用凭据，不修改对方文件）。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

BASE_DIR = Path(__file__).resolve().parent

# 依次尝试加载（后加载的不会覆盖已有的，override=False）：
#   backend/.env（后端目录）→ marketing-agent/.env（项目根，docker compose 读这份）
#   → ZT-agent/.env（共享同一份 DB 凭据）
_ENV_CANDIDATES = [
    BASE_DIR / ".env",
    BASE_DIR.parent / ".env",
    BASE_DIR.parent.parent / "ZT-agent" / ".env",
]
for _p in _ENV_CANDIDATES:
    if _p.exists():
        load_dotenv(_p, override=False)

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "agent_db")

_DB_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)

def build_engine(echo: bool = False) -> Engine:
    return create_engine(
        _DB_URL,
        pool_pre_ping=True,     # 断线自动重连
        pool_recycle=1800,      # 30 分钟回收，规避 MySQL wait_timeout
        echo=echo,
    )


read_engine: Engine = build_engine()
write_engine: Engine = build_engine()

# 写操作关键字（只读引擎上出现即拦截）
_WRITE_HEADS = ("insert", "update", "delete", "drop", "alter", "truncate", "create", "replace")


@event.listens_for(read_engine, "before_cursor_execute")
def _guard_readonly(conn, cursor, statement, parameters, context, executemany):
    """只读守卫：read_engine 上任何写语句立即抛错。

    这样「营销侧只读业务表」从设计约定变成运行时可验证的硬约束——
    即使将来有人误写，也会在测试阶段就暴露，而不是悄悄污染 ZT-agent 的数据。
    """
    head = statement.lstrip().lower()
    if head.startswith(_WRITE_HEADS):
        raise PermissionError(
            f"[read_engine] 只读连接禁止写操作，已拦截：{statement[:120]}"
        )


ReadSession = sessionmaker(bind=read_engine, autoflush=False, expire_on_commit=False)
WriteSession = sessionmaker(bind=write_engine, autoflush=False, expire_on_commit=False)


def get_read_session():
    """FastAPI 依赖：只读会话（业务数据）"""
    db = ReadSession()
    try:
        yield db
    finally:
        db.close()


def get_write_session():
    """FastAPI 依赖：写入会话（仅自管表 marketing_drafts）"""
    db = WriteSession()
    try:
        yield db
    finally:
        db.close()
