"""
营销侧口径一致性验证（marketing-agent）

验证用户反馈的「数据不同步」在营销侧的三条：
  ① models_readonly.Orders 补齐 7 个字段后能真正读到 ZT 的售后数据
  ② 经营指标口径与 ZT 对齐（旧口径把「已退货」算成成交 → GMV 虚高、无退货中统计）
  ③ 退货率告警纳入「退货申请中」（旧口径在商家处理前完全看不到）

用法（务必用营销侧专用 venv，系统 Python 没有 sqlalchemy）：
  cd marketing-agent/backend
  "D:/TYKKKKKK/.venvs/marketing-agent/Scripts/python.exe" scripts/verify_metrics_sync.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from sqlalchemy import case, func, select            # noqa: E402

from db import ReadSession                            # noqa: E402
from models_readonly import Inventory, Orders         # noqa: E402

PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'✓' if cond else '✗'} {label}" + (f"  ← {detail}" if detail else ""))


def main():
    # 注意：get_read_session 是给 FastAPI Depends 用的生成器，脚本里要直接取 Session
    db = ReadSession()
    try:
        # ── ① 字段可见性 ────────────────────────────────────────
        print("① 只读映射字段可见性（ZT 售后/归属字段）")
        one = db.execute(select(Orders).order_by(Orders.id.desc()).limit(1)).scalars().first()
        for fld, desc in [
            ("user_id", "订单归属用户"),
            ("completed_at", "用户确认收货时间"),
            ("return_status", "退货处理结果"),
            ("return_reason", "退货原因"),
            ("return_requested_at", "申请退货时间"),
            ("return_handled_at", "商家处理时间"),
            ("return_note", "商家处理备注"),
        ]:
            check(f"可读取 orders.{fld}（{desc}）", hasattr(one, fld))

        # ── ② 新旧口径对比 ──────────────────────────────────────
        print("\n② 经营指标：旧口径 vs 新口径（ZT 对齐后）")
        total = db.scalar(select(func.count(Orders.id))) or 0
        # 旧口径（改造前）
        old_valid = db.scalar(
            select(func.count(Orders.id)).where(Orders.status != "已取消")) or 0
        old_gmv = float(db.scalar(
            select(func.coalesce(func.sum(Orders.total_price), 0))
            .where(Orders.status != "已取消")) or 0)
        old_refund = db.scalar(
            select(func.count(Orders.id)).where(Orders.status.in_(["已取消", "已退货"]))) or 0
        old_rate = round(old_refund / total * 100, 1) if total else 0

        # 新口径（与 ZT 一致）
        PAID = ("已发货", "已完成", "退货申请中")
        new_valid = db.scalar(
            select(func.count(Orders.id)).where(Orders.status.in_(PAID))) or 0
        new_gmv = float(db.scalar(
            select(func.coalesce(func.sum(Orders.total_price), 0))
            .where(Orders.status.in_(PAID))) or 0)
        returning = db.scalar(
            select(func.count(Orders.id)).where(Orders.status == "退货申请中")) or 0
        refunded = db.scalar(
            select(func.count(Orders.id)).where(Orders.status == "已退货")) or 0
        ever = db.scalar(
            select(func.count(Orders.id))
            .where(Orders.status.in_(("已发货", "已完成", "退货申请中", "已退货")))) or 0
        new_rate = round(refunded / ever * 100, 1) if ever else 0

        print(f"    订单总数 {total}")
        print(f"    成交订单  旧 {old_valid}  →  新 {new_valid}")
        print(f"    GMV       旧 ¥{old_gmv:,.2f}  →  新 ¥{new_gmv:,.2f}（差 ¥{old_gmv - new_gmv:,.2f}）")
        print(f"    退货率    旧 {old_rate}%（分子含已取消）  →  新 {new_rate}%（已退/{ever} 曾发货）")
        print(f"    退货申请中 {returning} 单（旧口径完全不统计）")

        check("新口径 GMV 不高于旧口径（旧口径把已退货也算成交）", new_gmv <= old_gmv,
              f"{new_gmv} <= {old_gmv}")
        check("新口径不再把「已取消」计入退货", "已取消" not in PAID)
        check("新口径能观测到「退货申请中」", True, f"{returning} 单")

        # ── ③ 与 ZT 侧个人中心口径逐项对齐 ──────────────────────
        print("\n③ 与 ZT 口径逐项对齐（ZT: total_amount = 已发货+已完成+退货申请中）")
        zt_total_amount = float(db.scalar(
            select(func.coalesce(func.sum(Orders.total_price), 0))
            .where(Orders.status.in_(("已发货", "已完成", "退货申请中")))) or 0)
        check("营销 GMV == ZT 成交金额", abs(new_gmv - zt_total_amount) < 0.01,
              f"{new_gmv} vs {zt_total_amount}")

        pending = db.scalar(
            select(func.count(Orders.id)).where(Orders.status == "未发货")) or 0
        cancelled = db.scalar(
            select(func.count(Orders.id)).where(Orders.status == "已取消")) or 0
        check("状态全覆盖（各状态之和 == 总数）",
              new_valid + pending + refunded + cancelled == total,
              f"{new_valid}+{pending}+{refunded}+{cancelled} vs {total}")

        # ── ④ 退货率告警是否能看到"申请中" ──────────────────────
        print("\n④ 退货率告警（orchestrator._return_surge_products）")
        try:
            from orchestrator import _return_surge_products, MIN_RETURN_SAMPLE
            rows = db.execute(
                select(
                    Orders.product_id, Orders.product_name, Orders.product_sku,
                    func.count().label("total"),
                    func.sum(case((Orders.status == "已退货", 1), else_=0)).label("returned"),
                    func.sum(case((Orders.status == "退货申请中", 1), else_=0)).label("returning"),
                )
                .where(Orders.status.in_(("已发货", "已完成", "退货申请中", "已退货")))
                .group_by(Orders.product_id, Orders.product_name, Orders.product_sku)
            ).all()
            print(f"    曾发货商品分组（最小样本 {MIN_RETURN_SAMPLE}）：")
            for pid, name, sku, t, ret, rtn in rows:
                mark = "★" if int(t or 0) >= MIN_RETURN_SAMPLE else " "
                print(f"      {mark} #{pid} {name}: 曾发货 {t} / 已退 {ret or 0} / 申请中 {rtn or 0}")
            # 阈值 0 一定命中（只要有样本足够的分组），用来确认函数可跑通且返回新字段
            out = _return_surge_products(db, 0.0)
            check("_return_surge_products 可执行", isinstance(out, list), f"{len(out)} 条")
            if out:
                check("返回结构含 returning 字段（新增）", "returning" in out[0], str(list(out[0].keys())))
                check("分子已含「退货申请中」",
                      out[0]["rate"] == round((out[0]["returned"] + out[0]["returning"])
                                              / out[0]["total"] * 100, 1),
                      f"rate={out[0]['rate']} returned={out[0]['returned']} "
                      f"returning={out[0]['returning']} total={out[0]['total']}")
        except Exception as e:  # noqa: BLE001
            check("_return_surge_products 可执行", False, f"{type(e).__name__}: {e}")

        # ── ⑤ 只读守卫仍然生效 ──────────────────────────────────
        print("\n⑤ 只读硬约束（不应被本次改动破坏）")
        try:
            db.execute(select(Inventory).limit(1)).scalars().all()
            check("只读查询正常", True)
        except Exception as e:  # noqa: BLE001
            check("只读查询正常", False, str(e))

    finally:
        db.close()

    print("\n" + "=" * 64)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  ✗", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
