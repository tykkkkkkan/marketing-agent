"""
营销自动化接口 —— 文案生成 + 草稿审核闭环
────────────────────────────────────────────
闭环设计（面试可讲的安全边界）：
  AI 生成  →  落库为「待审核」草稿  →  人工审核  →  通过才可发布
AI 只是提效工具，最终对外内容必须过人这一关。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import get_write_session
from drafts import MarketingDraft

router = APIRouter(prefix="/api/marketing", tags=["营销自动化"])

VALID_STATUS = ("已通过", "已驳回")


class GenerateRequest(BaseModel):
    brief: str = Field(..., min_length=2, max_length=1000, description="运营需求")
    channel: str = Field("朋友圈", max_length=50, description="投放渠道")
    tone: str = Field("促销", max_length=50, description="文案语气")
    title: str = Field("", max_length=200, description="草稿标题（可留空自动生成）")
    extra: str = Field("", max_length=500, description="补充要求")


class ReviewRequest(BaseModel):
    status: str = Field(..., description="已通过 或 已驳回")
    reviewer: str = Field("", max_length=50, description="审核人")
    review_note: str = Field("", max_length=200, description="审核意见")


def _draft_dict(d: MarketingDraft) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "channel": d.channel,
        "tone": d.tone,
        "brief": d.brief,
        "content": d.content,
        "status": d.status,
        "reviewer": d.reviewer,
        "review_note": d.review_note,
        "created_at": d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "",
    }


@router.get("/llm-status", summary="大模型配置状态")
def llm_status():
    """检查 LangChain / DeepSeek 是否就绪（不触发调用）"""
    try:
        from marketing_agent import DEEPSEEK_MODEL, is_llm_ready
    except ImportError as e:
        return {
            "success": False,
            "message": f"LangChain 依赖未就绪：{e}",
            "data": {"ready": False},
        }
    return {
        "success": True,
        "message": "ok",
        "data": {"ready": is_llm_ready(), "model": DEEPSEEK_MODEL},
    }


@router.post("/generate", summary="生成营销文案（自动存为待审核草稿）")
def generate(req: GenerateRequest, db: Session = Depends(get_write_session)):
    try:
        from marketing_agent import generate_campaign
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"LangChain 依赖未就绪：{e}")

    try:
        content = generate_campaign(req.brief, req.channel, req.tone, req.extra)
    except Exception:
        # 不把底层异常细节透给前端，只给出可操作的提示
        raise HTTPException(
            status_code=502,
            detail="文案生成失败，请检查 DEEPSEEK_API_KEY 与网络连通性",
        )

    draft = MarketingDraft(
        title=req.title or f"{req.channel}文案 · {req.brief[:20]}",
        channel=req.channel,
        tone=req.tone,
        brief=req.brief,
        content=content,
        status="待审核",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return {"success": True, "message": "已生成草稿，待人工审核", "data": _draft_dict(draft)}


@router.get("/drafts", summary="草稿列表")
def list_drafts(
    status: str = Query("", description="按状态筛选：待审核/已通过/已驳回，留空为全部"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_write_session),
):
    stmt = select(MarketingDraft).order_by(MarketingDraft.created_at.desc()).limit(limit)
    if status:
        stmt = (
            select(MarketingDraft)
            .where(MarketingDraft.status == status)
            .order_by(MarketingDraft.created_at.desc())
            .limit(limit)
        )
    rows = db.scalars(stmt).all()
    return {"success": True, "message": "ok", "data": [_draft_dict(d) for d in rows]}


@router.post("/drafts/{draft_id}/review", summary="审核草稿（人工放行 / 驳回）")
def review(draft_id: int, req: ReviewRequest, db: Session = Depends(get_write_session)):
    if req.status not in VALID_STATUS:
        raise HTTPException(status_code=400, detail="status 只能是「已通过」或「已驳回」")
    draft = db.get(MarketingDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")
    draft.status = req.status
    draft.reviewer = req.reviewer
    draft.review_note = req.review_note
    db.commit()
    db.refresh(draft)
    return {"success": True, "message": f"审核完成：{req.status}", "data": _draft_dict(draft)}


@router.delete("/drafts/{draft_id}", summary="删除草稿")
def delete_draft(draft_id: int, db: Session = Depends(get_write_session)):
    draft = db.get(MarketingDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")
    db.delete(draft)
    db.commit()
    return {"success": True, "message": "已删除", "data": None}


@router.get("/stats", summary="草稿状态统计")
def stats(db: Session = Depends(get_write_session)):
    rows = db.execute(
        select(MarketingDraft.status, func.count(MarketingDraft.id)).group_by(MarketingDraft.status)
    ).all()
    data = {s: int(c) for s, c in rows}
    data["total"] = sum(data.values())
    return {"success": True, "message": "ok", "data": data}
