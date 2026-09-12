"""
营销自动化 Agent —— 后端入口（FastAPI）
─────────────────────────────────────────
设计原则（与 ZT-agent 解耦）：
  · 只读连接 ZT-agent 的 agent_db，业务表一行不写（db.py 内置只读守卫）；
  · 自身新增表（marketing_drafts / operation_tasks / ...）才允许写入；
  · 需要改动业务数据时（补货/发货/售后），以「后台操作员」身份调用
    ZT-agent 自己的管理接口（zt_client.py），由对方保障业务规则与事务；
  · 大模型能力由 LangChain 接入（marketing_agent.py）。
"""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import analytics, marketing, operations


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动钩子：创建自管表 + 启动自动运营调度器（均为幂等/可关）"""
    try:
        from drafts import init_own_tables

        init_own_tables()
        print("[startup] 自管表 marketing_drafts 就绪")
    except Exception as e:  # 数据库暂不可用时不阻塞服务启动
        print(f"[warn] 自管表初始化跳过：{type(e).__name__}: {e}")
    try:
        from operations import init_operation_tables

        init_operation_tables()
        print("[startup] 运营执行层自管表就绪（operation_tasks / action_audit_log / "
              "autonomy_settings / autopilot_runs）")
    except Exception as e:
        print(f"[warn] 运营执行层建表跳过：{type(e).__name__}: {e}")

    # 自动运营调度器：默认关闭（策略 autopilot_enabled=off），可被环境变量强制停用
    task = None
    if (os.getenv("AUTOPILOT_DISABLED") or "").lower() not in ("1", "true", "on", "yes"):
        try:
            from autopilot import scheduler_loop

            task = asyncio.create_task(scheduler_loop())
        except Exception as e:
            print(f"[warn] 自动运营调度器未启动：{type(e).__name__}: {e}")

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(
    title="中渔小助 · 营销自动化 Agent",
    version="0.5.0",
    description=(
        "面向 B 端的营销自动化服务：\n\n"
        "· **只读消费** ZT-agent 的业务数据（库存/订单/咨询/知识库）；\n"
        "· 由 LangChain 编排生成营销文案，经人工审核后发布；\n"
        "· **运营执行层**：把 AI 建议变成可审批、可执行、可追溯的工单，"
        "支持补货 / 发货 / 售后等动作的自主化分级（L0~L3）与护栏管控；\n"
        "· **自动运营**：可开启定时巡检，按策略自动干活并出报告（默认关闭）。"
    ),
    lifespan=lifespan,
)

# CORS：允许 React 前端（Vite 开发 5173 / 预览 4173）跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analytics.router)
app.include_router(marketing.router)
app.include_router(operations.router)


@app.get("/api/health", tags=["系统"])
def health():
    """健康检查"""
    return {"success": True, "service": "marketing-agent", "status": "ok"}
