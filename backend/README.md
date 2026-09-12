# 营销自动化 Agent · 后端（FastAPI）

> 项目总览、架构图、接口清单、端到端验证结果与踩坑记录，请看**上级目录的 [`../README.md`](../README.md)**。
> 本文件只保留后端专属信息。

## 目录结构

```
backend/
├── main.py                 FastAPI 入口（CORS + lifespan 建自管表 + 挂载路由）
├── db.py                   数据层：只读引擎（带写操作守卫）+ 写入引擎
├── models_readonly.py      ZT-agent 业务表只读 ORM 映射
├── marketing_tools.py      LangChain 工具（4 个，全部只读）
├── marketing_agent.py      LangChain 编排（1.x create_agent + DeepSeek）
├── drafts.py               自管表 marketing_drafts（文案草稿）
├── operations.py           运营执行层：动作注册表 + 护栏引擎 + 任务状态机 + 审计 + 自动运营
├── autopilot.py            自动运营调度器（无人值守：进程内 asyncio 循环，默认关闭）
├── zt_client.py            执行器：以「后台操作员」身份调用 ZT-agent 管理接口（HTTP + JWT）
├── routers/
│   ├── analytics.py        经营分析接口（销量/库存/概览/咨询热点/经营诊断）
│   ├── marketing.py        文案生成 + 草稿审核闭环接口
│   └── operations.py       运营任务接口（巡检/审批/执行/策略/急停/审计/自动运营）
├── requirements.txt        实际锁定版本（见文件头注释）
├── Dockerfile
└── .env.example
```

## 运行为什么需要 ZT-agent 也起着

营销 Agent 对业务数据分两种处理，边界很清楚：

| 诉求 | 做法 | 谁保障正确性 |
|---|---|---|
| **看数据**（销量/库存/订单/咨询） | 直接只读 `agent_db` | 本服务的只读守卫 |
| **改数据**（补货/发货/售后） | 调用 ZT-agent 已有的管理接口（HTTP + JWT） | **ZT-agent 自己**的事务与业务规则 |

第二条是关键：营销侧不去写 `inventory.stock`，而是请 ZT-agent 去写 —— 库存扣减、订单状态机、
钱包记账这些事务逻辑一份都不会被绕过，ZT-agent 也不用为营销 Agent 改任何代码。
ZT-agent 没起时，只有「执行」能力不可用，看板、文案等功能不受影响。

## 「只读」是如何落地的（面试可讲）

`db.py` 在 `read_engine` 上挂了 SQLAlchemy 的 `before_cursor_execute` 事件钩子：
任何以 `insert/update/delete/drop/alter/create/truncate/replace` 开头的语句都会抛
`PermissionError`。也就是说「营销侧只读业务表」**不是口头约定，而是运行时可验证的硬约束** ——
将来万一手滑写了写操作，会在测试阶段就暴露，而不是悄悄污染 ZT-agent 的数据。

写入走另一条 `write_engine`，且只被自管表使用（`marketing_drafts` / `operation_tasks` /
`action_audit_log` / `autonomy_settings` / `autopilot_runs`）。

配置加载顺序（`db.py` 与 `zt_client.py` 保持一致）：
`backend/.env → marketing-agent/.env → ZT-agent/.env`，都缺失时才落到代码内置默认值。

## 自动运营会占资源吗

不会。`autopilot.py` 的调度循环每 **60 秒**才醒一次判断「是否到点」，到点才真的干活；
数据库操作走 `asyncio.to_thread`，不阻塞事件循环；循环体整体 try/except，任何异常只打印不抛出。
不引入任何新依赖（标准库 `asyncio` + 已有的同步 SQLAlchemy 会话）。
用 `AUTOPILOT_DISABLED=1` 可彻底不启动它。

## 依赖与启动

见 [`../README.md`](../README.md) 第三节。要点：**Python 3.12 + 无中文路径的 venv + 清华源**。

## 快速自检

```bash
curl http://127.0.0.1:8010/api/analytics/health-db      # 数据库连通性
curl http://127.0.0.1:8010/api/marketing/llm-status     # 大模型就绪状态
curl http://127.0.0.1:8010/api/marketing/stats          # 草稿状态统计
curl http://127.0.0.1:8010/api/operations/zt-status     # 收银系统连通性 + 凭据 + 执行模式
curl http://127.0.0.1:8010/api/operations/tasks         # 运营待办列表
curl http://127.0.0.1:8010/api/operations/auto-pilot    # 自动运营状态 + 最近运行记录
```
