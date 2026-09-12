"""
自动运营调度器 —— 让 Agent 在无人值守时也能按策略工作
════════════════════════════════════════════════════════
用户诉求原话：「前期可以向我请求，后期成熟了，自己运营就行」。

这就是那个「后期」：进程内起一个轻量调度循环，按设定间隔自动跑一轮
「巡检 → 建单 → 按护栏执行 → 出报告」，人只需要看报告、处理转人工的部分。

三条安全设计
────────────
1. **默认关闭**：`autopilot_enabled = off`，必须由人在页面上显式打开。
2. **不新增权限**：自动运营只是「替人按下巡检按钮」，能自动执行什么完全由
   自主化策略与七道护栏决定 —— 发货/退货/取消依旧一律转人工。
3. **绝不拖垮服务**：循环体整体 try/except，异常只打印不抛出；
   数据库操作走 `asyncio.to_thread`，不阻塞事件循环。

零新增依赖：只用标准库 asyncio + 已有的同步 SQLAlchemy 会话。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from db import ReadSession, WriteSession
from operations import get_policy, last_timer_run_at, run_autopilot

TICK_SECONDS = 60          # 每 60 秒检查一次「是否到点」
STARTUP_DELAY = 8          # 启动后先等应用完全就绪
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 24 * 60


def _compute_interval(policy: dict) -> int:
    try:
        n = int(policy.get("autopilot_interval_minutes") or 30)
    except (TypeError, ValueError):
        n = 30
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, n))


def next_run_at(policy: dict) -> datetime | None:
    """下一次自动运营的时间（供页面展示；未开启或读不到历史时返回 None）"""
    if policy.get("autopilot_enabled") != "on":
        return None
    db = WriteSession()
    try:
        last = last_timer_run_at(db)
    except Exception:
        last = None
    finally:
        db.close()
    interval = _compute_interval(policy)
    if last is None:
        return datetime.now()          # 还没跑过 → 下一轮就到
    return last + timedelta(minutes=interval)


def _tick() -> None:
    """同步执行一轮判断（在线程里跑，避免阻塞事件循环）"""
    wdb = WriteSession()
    try:
        policy = get_policy(wdb)
        if policy.get("autopilot_enabled") != "on":
            return
        interval = _compute_interval(policy)
        last = last_timer_run_at(wdb)
        now = datetime.now()
        if last is not None and (now - last) < timedelta(minutes=interval):
            return
        rdb = ReadSession()
        try:
            result = run_autopilot(wdb, rdb, trigger="timer")
        finally:
            rdb.close()
        print(f"[autopilot] 第 {result.get('run_id')} 轮完成：{result.get('message')}")
    finally:
        wdb.close()


async def scheduler_loop() -> None:
    """调度循环（由 FastAPI lifespan 启动，随应用关闭而取消）"""
    await asyncio.sleep(STARTUP_DELAY)
    print(f"[autopilot] 调度器就绪，每 {TICK_SECONDS}s 检查一次（默认关闭，可在页面开启）")
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            print("[autopilot] 调度器已停止")
            raise
        except Exception as e:  # noqa: BLE001 —— 任何异常都不能让循环退出
            print(f"[autopilot] 本轮异常已忽略：{type(e).__name__}: {e}")
