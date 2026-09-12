"""
运营执行 API —— 让运营人员「看诊断 → 批任务 → 真执行」
═══════════════════════════════════════════════════════
接口分组：
  · 动作与策略   GET  /actions           动作目录（含风险等级说明）
                 GET  /policy            当前自主化策略
                 PUT  /policy            修改策略（等级/限额/白名单/执行模式）
                 POST /kill-switch       急停开关（一键暂停所有自动执行）
  · 巡检与任务   POST /scan              只读巡检 → 生成待办（护栏决定自动/人工）
                 GET  /tasks             任务列表（按状态/动作筛选）
                 GET  /tasks/{id}        任务详情（含护栏判定 + 审计轨迹）
                 POST /tasks             手工建任务
                 PATCH /tasks/{id}       待审批阶段补充参数（如运单号）
  · 审批与执行   POST /tasks/{id}/approve   批准（可立即执行）
                 POST /tasks/{id}/reject    驳回
                 POST /tasks/{id}/cancel    撤销
                 POST /tasks/{id}/execute   对已批准任务执行
  · 追溯         GET  /audit             审计日志（谁、何时、做了什么）
                 GET  /stats             状态统计
                 GET  /zt-status         ZT-agent 连通性与凭据状态
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import get_read_session, get_write_session
from autopilot import next_run_at
from operations import (
    ACTION_REGISTRY, AUTONOMY_LEVELS, POLICY_DEFAULTS, POLICY_LABELS,
    STATUS_APPROVED, STATUS_CANCELLED, STATUS_DONE, STATUS_FAILED,
    STATUS_PENDING, STATUS_REJECTED,
    ActionAudit, OperationTask, action_catalog, approve_task, autopilot_runs,
    cancel_task, create_task, create_tasks_from_scan, execute_task, get_policy,
    reject_task, run_autopilot, scan_candidates, set_policy, task_dict,
    today_auto_count, update_payload,
)
from zt_client import ZTAgentError, client as zt

router = APIRouter(prefix="/api/operations", tags=["运营执行"])

ALL_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_DONE, STATUS_FAILED,
                STATUS_REJECTED, STATUS_CANCELLED)


# ════════════════════════════════════════════════════════════════
# 请求模型
# ════════════════════════════════════════════════════════════════
class PolicyUpdate(BaseModel):
    global_level: str | None = Field(None, description="L0 仅建议 / L1 人工审批 / L2 限额内自动 / L3 白名单全自动")
    kill_switch: str | None = Field(None, description="off / on")
    execute_mode: str | None = Field(None, description="live 真实执行 / dry_run 演练")
    auto_actions: str | None = Field(None, description="允许自动执行的动作，逗号分隔")
    auto_target_whitelist: str | None = Field(None, description="目标白名单，逗号分隔；空=不限制")
    auto_restock_max_qty: str | None = Field(None, description="L2 单次自动补货数量上限")
    auto_restock_max_amount: str | None = Field(None, description="L2 单次自动补货金额上限（元）")
    daily_auto_quota: str | None = Field(None, description="每日自动执行次数配额")
    autopilot_enabled: str | None = Field(None, description="on/off —— 是否开启定时自动运营")
    autopilot_interval_minutes: str | None = Field(None, description="自动运营间隔（分钟）")
    actor: str = Field("运营", max_length=50, description="操作人")


class KillSwitchRequest(BaseModel):
    on: bool = Field(..., description="true=开启急停（全部转人工），false=解除")
    actor: str = Field("运营", max_length=50)


class TaskCreateRequest(BaseModel):
    action_code: str = Field(..., description="restock / ship / return / cancel")
    payload: dict = Field(default_factory=dict, description="动作参数")
    reason: str = Field("", max_length=500, description="发起原因")
    title: str = Field("", max_length=200)
    target_label: str = Field("", max_length=120)
    quantity: int = Field(0, ge=0)
    amount: float = Field(0.0, ge=0)
    actor: str = Field("运营", max_length=50)


class TaskActionRequest(BaseModel):
    actor: str = Field("运营", max_length=50)
    note: str = Field("", max_length=200)
    execute_now: bool = Field(True, description="批准后是否立即执行")


class PayloadPatchRequest(BaseModel):
    payload: dict = Field(default_factory=dict)


def _get_task(db: Session, task_id: int) -> OperationTask:
    task = db.get(OperationTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")
    return task


def _guard(fn, *args, **kwargs):
    """把领域层 ValueError 转成 400，避免 500 泄露内部细节"""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ZTAgentError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ════════════════════════════════════════════════════════════════
# 动作目录 / 策略 / 急停
# ════════════════════════════════════════════════════════════════
@router.get("/actions", summary="动作目录（系统能做哪些事）")
def list_actions():
    """返回动作注册表 + 自主等级说明，前端据此渲染「新建任务」表单与图例。"""
    return {
        "success": True,
        "message": "ok",
        "data": {
            "actions": action_catalog(),
            "levels": [{"code": k, "name": v[0], "desc": v[1]} for k, v in AUTONOMY_LEVELS.items()],
        },
    }


@router.get("/policy", summary="读取自主化策略")
def read_policy(db: Session = Depends(get_write_session)):
    policy = get_policy(db)
    return {
        "success": True,
        "message": "ok",
        "data": {
            "policy": policy,
            "labels": POLICY_LABELS,
            "defaults": POLICY_DEFAULTS,
            "today_auto_count": today_auto_count(db),
        },
    }


@router.put("/policy", summary="修改自主化策略")
def write_policy(req: PolicyUpdate, db: Session = Depends(get_write_session)):
    updates = req.model_dump(exclude={"actor"}, exclude_none=True)
    if "global_level" in updates and updates["global_level"] not in AUTONOMY_LEVELS:
        raise HTTPException(status_code=400, detail=f"未知自主等级：{updates['global_level']}")
    policy = set_policy(db, updates, actor=req.actor)
    return {"success": True, "message": "策略已更新", "data": policy}


@router.post("/kill-switch", summary="急停开关")
def kill_switch(req: KillSwitchRequest, db: Session = Depends(get_write_session)):
    """开启后：所有动作（含护栏内本可自动执行的）一律转人工审批。"""
    policy = set_policy(db, {"kill_switch": "on" if req.on else "off"}, actor=req.actor)
    msg = "急停已开启：全部动作转人工审批" if req.on else "急停已解除：恢复按护栏自动判定"
    return {"success": True, "message": msg, "data": policy}


@router.get("/zt-status", summary="ZT-agent 连通性与凭据状态")
def zt_status():
    """检查 ZT-agent 是否可达、管理员凭据是否可用、当前是真实执行还是演练模式。"""
    return {"success": True, "message": "ok", "data": zt.status()}


# ════════════════════════════════════════════════════════════════
# 巡检
# ════════════════════════════════════════════════════════════════
@router.get("/scan-preview", summary="巡检预览（只读，不建任务）")
def scan_preview(read_db: Session = Depends(get_read_session)):
    """只看不建：让运营先看清楚「AI 打算做什么」，再决定要不要生成待办。"""
    try:
        data = scan_candidates(read_db)
    except Exception:
        raise HTTPException(status_code=503, detail="业务数据库暂不可用，请稍后重试")
    return {"success": True, "message": "ok", "data": data}


@router.post("/scan", summary="智能巡检：生成运营待办")
def scan(
    auto_run: bool = Query(True, description="是否允许护栏内的动作自动执行"),
    actor: str = Query("系统巡检", max_length=50),
    read_db: Session = Depends(get_read_session),
    write_db: Session = Depends(get_write_session),
):
    """只读扫描 ZT-agent 业务数据（库存/订单/咨询），产出：
       · 补货待办（库存低于预警线）
       · 发货待办（未发货订单）
       · 售后线索（客户提到退换货/质量问题的咨询，仅提示，不自动建单）
    每条待办都已过护栏判定：能自动的已自动执行，其余的挂在「待审批」等人点头。
    """
    try:
        result = create_tasks_from_scan(write_db, read_db, actor=actor)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"巡检失败：{type(e).__name__}")
    if not auto_run:
        result["auto_executed_task_ids"] = []
    return {"success": True, "message": "巡检完成，待办已生成", "data": result}


# ════════════════════════════════════════════════════════════════
# 任务
# ════════════════════════════════════════════════════════════════
@router.get("/tasks", summary="任务列表")
def list_tasks(
    status: str = Query("", description="按状态筛选，空=全部"),
    action_code: str = Query("", description="按动作筛选"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_write_session),
):
    stmt = select(OperationTask)
    if status:
        if status not in ALL_STATUSES:
            raise HTTPException(status_code=400, detail=f"未知状态：{status}")
        stmt = stmt.where(OperationTask.status == status)
    if action_code:
        stmt = stmt.where(OperationTask.action_code == action_code)
    stmt = stmt.order_by(OperationTask.id.desc()).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return {"success": True, "message": "ok",
            "data": {"total": len(rows), "items": [task_dict(t) for t in rows]}}


@router.get("/tasks/{task_id}", summary="任务详情（含审计轨迹）")
def task_detail(task_id: int, db: Session = Depends(get_write_session)):
    task = _get_task(db, task_id)
    audits = db.execute(
        select(ActionAudit).where(ActionAudit.task_id == task_id)
        .order_by(ActionAudit.id.asc())
    ).scalars().all()
    data = task_dict(task)
    data["audit"] = [
        {"event": a.event, "actor": a.actor, "detail": a.detail,
         "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else ""}
        for a in audits
    ]
    return {"success": True, "message": "ok", "data": data}


@router.post("/tasks", summary="手工创建任务")
def create(req: TaskCreateRequest, db: Session = Depends(get_write_session)):
    if req.action_code not in ACTION_REGISTRY:
        raise HTTPException(status_code=400, detail=f"未知动作：{req.action_code}")
    task = _guard(create_task, db,
                  action_code=req.action_code, payload=req.payload, reason=req.reason,
                  title=req.title, target_label=req.target_label, quantity=req.quantity,
                  amount=req.amount, source="manual", actor=req.actor)
    db.refresh(task)
    return {"success": True, "message": f"任务 #{task.id} 已创建（{task.status}）",
            "data": task_dict(task)}


@router.patch("/tasks/{task_id}", summary="补充任务参数（待审批阶段）")
def patch_task(task_id: int, req: PayloadPatchRequest, db: Session = Depends(get_write_session)):
    task = _get_task(db, task_id)
    _guard(update_payload, db, task, req.payload)
    db.refresh(task)
    return {"success": True, "message": "参数已更新", "data": task_dict(task)}


@router.post("/tasks/{task_id}/approve", summary="批准（可立即执行）")
def approve(task_id: int, req: TaskActionRequest, db: Session = Depends(get_write_session)):
    task = _get_task(db, task_id)
    _guard(approve_task, db, task, req.actor, req.note, req.execute_now)
    db.refresh(task)
    msg = f"已批准并{('执行完成' if task.status == STATUS_DONE else '提交执行')}：{task.exec_message or '—'}"
    return {"success": True, "message": msg, "data": task_dict(task)}


@router.post("/tasks/{task_id}/reject", summary="驳回")
def reject(task_id: int, req: TaskActionRequest, db: Session = Depends(get_write_session)):
    task = _get_task(db, task_id)
    _guard(reject_task, db, task, req.actor, req.note)
    db.refresh(task)
    return {"success": True, "message": "已驳回", "data": task_dict(task)}


@router.post("/tasks/{task_id}/cancel", summary="撤销")
def cancel(task_id: int, req: TaskActionRequest, db: Session = Depends(get_write_session)):
    task = _get_task(db, task_id)
    _guard(cancel_task, db, task, req.actor, req.note)
    db.refresh(task)
    return {"success": True, "message": "已撤销", "data": task_dict(task)}


@router.post("/tasks/{task_id}/execute", summary="执行已批准任务")
def execute(task_id: int, req: TaskActionRequest, db: Session = Depends(get_write_session)):
    task = _get_task(db, task_id)
    _guard(execute_task, db, task, req.actor, False)
    db.refresh(task)
    return {"success": True, "message": task.exec_message or "已执行", "data": task_dict(task)}


# ════════════════════════════════════════════════════════════════
# 审计 / 统计
# ════════════════════════════════════════════════════════════════
@router.get("/audit", summary="审计日志")
def audit_log(limit: int = Query(50, ge=1, le=500),
              db: Session = Depends(get_write_session)):
    rows = db.execute(
        select(ActionAudit).order_by(ActionAudit.id.desc()).limit(limit)
    ).scalars().all()
    return {"success": True, "message": "ok", "data": [
        {"id": a.id, "task_id": a.task_id, "action_code": a.action_code, "event": a.event,
         "actor": a.actor, "detail": a.detail,
         "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else ""}
        for a in rows
    ]}


@router.get("/stats", summary="任务状态统计")
def stats(db: Session = Depends(get_write_session)):
    rows = db.execute(
        select(OperationTask.status, func.count(OperationTask.id)).group_by(OperationTask.status)
    ).all()
    by_status = {s: 0 for s in ALL_STATUSES}
    for status_, cnt in rows:
        by_status[status_] = int(cnt or 0)
    total = sum(by_status.values())
    auto_cnt = int(db.execute(
        select(func.count(OperationTask.id)).where(OperationTask.auto_executed.is_(True))
    ).scalar() or 0)
    policy = get_policy(db)
    return {"success": True, "message": "ok", "data": {
        "total": total, "by_status": by_status,
        "auto_executed_total": auto_cnt,
        "today_auto_count": today_auto_count(db),
        "daily_auto_quota": policy.get("daily_auto_quota"),
        "global_level": policy.get("global_level"),
        "kill_switch": policy.get("kill_switch"),
        "execute_mode": policy.get("execute_mode"),
        "autopilot_enabled": policy.get("autopilot_enabled"),
        "autopilot_interval_minutes": policy.get("autopilot_interval_minutes"),
    }}


# ════════════════════════════════════════════════════════════════
# 自动运营（无人值守）
# ════════════════════════════════════════════════════════════════
@router.get("/auto-pilot", summary="自动运营状态")
def autopilot_status(db: Session = Depends(get_write_session)):
    """看自动运营是否开启、下一轮什么时候跑、最近几轮都干了什么。"""
    policy = get_policy(db)
    nxt = next_run_at(policy)
    return {"success": True, "message": "ok", "data": {
        "enabled": policy.get("autopilot_enabled") == "on",
        "interval_minutes": policy.get("autopilot_interval_minutes"),
        "next_run_at": nxt.strftime("%Y-%m-%d %H:%M") if nxt else "",
        "kill_switch": policy.get("kill_switch"),
        "runs": autopilot_runs(db, limit=10),
    }}


@router.post("/auto-pilot/run", summary="立即执行一轮自动运营")
def autopilot_run_now(
    trigger: str = Query("manual", description="manual 页面手动 / api 接口调用"),
    read_db: Session = Depends(get_read_session),
    write_db: Session = Depends(get_write_session),
):
    """手动跑一轮（不依赖定时器是否开启）——巡检 → 建单 → 按护栏执行 → 出报告。"""
    try:
        result = run_autopilot(write_db, read_db, trigger=trigger[:20] or "manual")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"自动运营执行失败：{type(e).__name__}")
    return {"success": result.get("success", True), "message": result.get("message", ""),
            "data": result}
