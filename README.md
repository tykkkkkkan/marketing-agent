# 中渔小助 · 营销自动化 Agent（B 端）

面向渔具企业「运营 / 老板」的营销自动化 Agent：**只读**消费 `ZT-agent`（C 端客服）的业务数据，
由 LangChain 编排大模型生成营销文案，经**人工审核**后才可发布。

> 定位：与 ZT-agent 构成「双 Agent 系统」——一个面向**客户咨询**（C 端），一个面向**企业营销**（B 端）。

## 一、架构（布局 A：兄弟项目，代码零耦合）

```
中渔天下/
├── ZT-agent/                  ← C 端客服 · Django5.2 + Vue3 + 自研 ReAct/RAG · 独立 git
└── marketing-agent/           ← B 端营销 · FastAPI + React + LangChain · 独立 git
    ├── backend/               FastAPI 服务（:8010）
    ├── frontend/              React 运营后台（Vite，:5173）
    ├── docs/                  PRD + 项目技术方案（Word）
    └── docker-compose.yml     容器编排（mkt + nginx）
```

**关联方式**：同一个 MySQL（`agent_db`，只读）+ 同一份 DeepSeek 密钥 —— 靠**数据契约**关联，不靠代码 import。
两个服务可各自独立部署、独立运行，任一方宕机不影响另一方。

## 二、技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | React 18 + Vite 6 + Axios | 纯 CSS，品牌色 `#1F6B54` |
| 后端 | FastAPI + SQLAlchemy 2.0 + PyMySQL | 只读引擎带写操作守卫 |
| 大模型 | LangChain 1.x（`create_agent`）+ DeepSeek `deepseek-chat` | 与 ZT-agent 共用 API Key |
| 数据 | 只读 ZT-agent 的 `agent_db`；自管表 `marketing_drafts` | 业务表一行不写 |

## 三、快速开始（本地开发）

> ⚠️ **虚拟环境必须建在无中文路径下** —— 本项目路径含「中渔天下」，实测 `uv pip install`
> 在中文路径下会**卡死**（3 分 42 秒零输出），换到 `D:/TYKKKKKK/.venvs/` 后 21 秒装完。

```bash
# 1) 后端：建 venv（无中文路径）并装依赖（走清华源，避免官方源超时）
uv venv --python 3.12 "D:/TYKKKKKK/.venvs/marketing-agent"
uv pip install --python "D:/TYKKKKKK/.venvs/marketing-agent/Scripts/python.exe" \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r backend/requirements.txt

# 2) 后端：启动（--app-dir 规避 cd 中文目录问题）
"D:/TYKKKKKK/.venvs/marketing-agent/Scripts/python.exe" -m uvicorn main:app \
  --host 127.0.0.1 --port 8010 \
  --app-dir "D:/TYKKKKKK/Trae/中渔天下/marketing-agent/backend"

# 3) 前端：装依赖并启动
cd frontend && npm install --registry=https://registry.npmmirror.com && npm run dev
```

- 运营后台：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8010/docs

**容器化（可选）**：在 `marketing-agent/` 放一份 `.env`（含 `DB_PASSWORD`、`DEEPSEEK_API_KEY`），
然后 `docker compose up -d --build`，访问 http://localhost:8080 。
容器内通过 `host.docker.internal` 连接宿主机上 ZT-agent 的 MySQL。

## 四、接口清单

### 经营分析（只读 ZT-agent 数据）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/analytics/health-db` | 数据库连通性自检 |
| GET | `/api/analytics/overview` | 经营概览（订单/GMV/钱包/待发货/库存预警数） |
| GET | `/api/analytics/sales-top` | 销量排行 Top N |
| GET | `/api/analytics/low-stock` | 库存预警 |
| GET | `/api/analytics/hot-questions` | 客户咨询热点挖掘 |

### 营销自动化

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/marketing/llm-status` | 大模型配置状态 |
| POST | `/api/marketing/generate` | 生成文案（Agent 自主查数据）→ 存为待审核草稿 |
| GET | `/api/marketing/drafts` | 草稿列表（可按状态筛选） |
| POST | `/api/marketing/drafts/{id}/review` | 人工审核（已通过 / 已驳回） |
| DELETE | `/api/marketing/drafts/{id}` | 删除草稿 |
| GET | `/api/marketing/stats` | 草稿状态统计 |

## 五、Agent 工具（LangChain）

| 工具 | 作用 |
|---|---|
| `get_sales_ranking` | 查销量排行 → 定主推商品 |
| `get_low_stock_products` | 查库存预警 → 避开缺货品 |
| `search_knowledge` | 检索企业知识库 → 取卖点素材 |
| `get_recent_customer_questions` | 看客户近期咨询 → 找营销切入点 |

**防幻觉设计**：所有数据必须由工具提供，系统提示明确禁止编造销量/价格/库存，也不允许承诺
「百分百上鱼」这类不可验证效果。

## 六、端到端验证结果（实测）

| 环节 | 结果 |
|---|---|
| 只读连通 agent_db | ✅ 读到真实数据：订单 14 单、GMV ¥7036、钱包余额 ¥900、库存预警 3 项 |
| 销量 Top5 | ✅ 速攻2号 100件 / 钓鱼王 30件 / 九一八 10件 / 红虫颗粒 6件 |
| Agent 生成文案 | ✅ 真实调工具后产出朋友圈/社群文案，数据与库一致，无编造 |
| 草稿闭环 | ✅ 生成 → 落库「待审核」→ 审核「已通过」→ 统计正确流转 |
| 前端链路 | ✅ 页面 200 + 经 Vite 代理成功拉到后端真实数据 |

## 七、设计约束

| 约束 | 说明 |
|---|---|
| **只读业务表** | 对 `orders` / `products` / `inventory` 等只 SELECT，`db.py` 内置守卫拦截任何写语句 |
| **写只走新表** | 只写自管表 `marketing_drafts` |
| **不改 ZT-agent** | ZT-agent 可独立 `git pull && docker compose up`，不受本项目影响 |
| **人工审核** | AI 生成内容必须经审核才可发布，不直接对外 |

## 八、踩坑记录

1. **uv 在中文路径下卡死** → venv 建到无中文路径（`D:/TYKKKKKK/.venvs/marketing-agent`）。
2. **PyPI 官方源极慢**（5 分钟无果）→ 用清华源 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`，29 秒装完。
3. **服务运行中装依赖会失败**（`orjson` 拒绝访问 / os error 5）→ 先停服务释放 venv 文件锁，再装。
4. **langchain 1.x 移除了旧 API**（`AgentExecutor` / `create_tool_calling_agent`）→ 改用官方新 API `langchain.agents.create_agent`。
5. **Git Bash 下 `taskkill //F` 无效**（`//` 被当路径）→ 用 `MSYS_NO_PATHCONV=1 taskkill /F /PID <pid>`。
6. **Vite 默认只监听 `[::1]`**，`127.0.0.1` 访问不到 → `vite.config.js` 显式 `host: '127.0.0.1'`。
