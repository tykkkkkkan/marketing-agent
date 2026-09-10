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
├── drafts.py               自管表 marketing_drafts（本服务唯一允许写的表）
├── routers/
│   ├── analytics.py        经营分析接口（销量/库存/概览/咨询热点）
│   └── marketing.py        文案生成 + 草稿审核闭环接口
├── requirements.txt        实际锁定版本（见文件头注释）
├── Dockerfile
└── .env.example
```

## 「只读」是如何落地的（面试可讲）

`db.py` 在 `read_engine` 上挂了 SQLAlchemy 的 `before_cursor_execute` 事件钩子：
任何以 `insert/update/delete/drop/alter/create/truncate/replace` 开头的语句都会抛
`PermissionError`。也就是说「营销侧只读业务表」**不是口头约定，而是运行时可验证的硬约束** ——
将来万一手滑写了写操作，会在测试阶段就暴露，而不是悄悄污染 ZT-agent 的数据。

写入走另一条 `write_engine`，且只被 `drafts.py`（自管表）使用。

## 依赖与启动

见 [`../README.md`](../README.md) 第三节。要点：**Python 3.12 + 无中文路径的 venv + 清华源**。

## 快速自检

```bash
curl http://127.0.0.1:8010/api/analytics/health-db    # 数据库连通性
curl http://127.0.0.1:8010/api/marketing/llm-status   # 大模型就绪状态
curl http://127.0.0.1:8010/api/marketing/stats        # 草稿状态统计
```
