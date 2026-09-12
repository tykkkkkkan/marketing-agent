# 中渔小助 · 营销自动化 Agent（B 端）

面向渔具企业「运营 / 老板」的营销自动化 Agent：

1. **看懂生意** —— **只读**消费 `ZT-agent`（C 端客服）的业务数据，生成经营诊断；
2. **写出内容** —— 由 LangChain 编排大模型生成营销文案，经**人工审核**后才可发布；
3. **真能办事** —— **运营执行层**把 AI 建议变成可审批、可执行、可追溯的工单，覆盖
   **补库存 / 订单发货 / 退货 / 取消**，并按自主化分级（L0~L3）决定「AI 自己干」还是「等你点头」。

> 定位：与 ZT-agent 构成「双 Agent 系统」——一个面向**客户咨询**（C 端），一个面向**企业营销**（B 端）。

## 一、架构（布局 A：兄弟项目，代码零耦合）

```
中渔天下/
├── ZT-agent/                  ← C 端客服 · Django5.2 + Vue3 + 自研 ReAct/RAG · 独立 git
└── marketing-agent/           ← B 端营销 · FastAPI + React + LangChain · 独立 git
    ├── backend/               FastAPI 服务（:8010）
    │   ├── operations.py      运营执行层：动作注册表 + 护栏引擎 + 任务状态机 + 审计
    │   ├── autopilot.py       自动运营调度器（无人值守，默认关闭）
    │   ├── zt_client.py       执行器：以「后台操作员」身份调用 ZT-agent 自身接口
    │   └── routers/           analytics / marketing / operations
    ├── frontend/              React 运营后台（Vite，:5173）
    ├── docs/                  PRD + 项目技术方案 + 运营执行层与 API 说明（Word）
    └── docker-compose.yml     容器编排（mkt + nginx）
```

**关联方式**：同一个 MySQL（`agent_db`，只读）+ 同一份 DeepSeek 密钥 + 同一套 ZT-agent 管理接口
—— 靠**数据契约 + 接口契约**关联，不靠代码 import。两个服务可各自独立部署、独立运行。

**改数据为什么不违规**：营销 Agent 从不写 `orders` / `inventory` 等业务表；需要变更业务数据时，
它以一个普通「后台操作员」的身份调用 ZT-agent **自己已有的**管理接口（HTTP + JWT），
由 ZT-agent 去执行库存扣减、订单状态机与钱包记账 —— 事务与业务规则仍由 ZT-agent 保障，
营销侧不可能绕过规则写出脏数据，ZT-agent 依然一行代码都不用改。

## 二、技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | React 18 + Vite 6 + Axios | 纯 CSS，品牌色 `#1F6B54` |
| 后端 | FastAPI + SQLAlchemy 2.0 + PyMySQL | 只读引擎带写操作守卫 |
| 大模型 | LangChain 1.x（`create_agent`）+ DeepSeek `deepseek-chat` | 与 ZT-agent 共用 API Key |
| 数据 | 只读 ZT-agent 的 `agent_db`；自管表 `marketing_drafts` / `operation_tasks` / `action_audit_log` / `autonomy_settings` / `autopilot_runs` | 业务表一行不写 |
| 执行 | HTTP + JWT 调用 ZT-agent 管理接口 | 标准库 `urllib`，零额外依赖 |
| 调度 | 进程内 `asyncio` 轻量循环 | 标准库，零额外依赖，默认关闭 |

## 三、快速开始（本地开发）

> ⚠️ **虚拟环境必须建在无中文路径下** —— 本项目路径含「中渔天下」，实测 `uv pip install`
> 在中文路径下会**卡死**（3 分 42 秒零输出），换到 `D:/TYKKKKKK/.venvs/` 后 21 秒装完。

```bash
# 0) 配置：复制模板并填 DB_PASSWORD / DEEPSEEK_API_KEY / ZT_STAFF_USER / ZT_STAFF_PASSWORD
cp .env.example .env

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
- API 文档（Swagger）：http://127.0.0.1:8010/docs

> **运营执行层需要 ZT-agent 也起着**：营销 Agent 通过它的管理接口办事。
> ZT-agent 没起时，页面上「收银系统」会显示未连接，补货/发货类操作会明确报错，其余功能不受影响。
> 在 ZT-agent 后台（`/admin/`）建一个 staff 机器人账号填进 `.env` 即可启用真实执行。

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

### 运营执行层（让 Agent 真去办事）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/operations/actions` | 动作目录（能做哪些事、风险等级、参数、是否允许自动化） |
| GET | `/api/operations/policy` | 读取自主化策略（等级/限额/配额/白名单/执行模式） |
| PUT | `/api/operations/policy` | 修改策略 |
| POST | `/api/operations/kill-switch` | **急停开关**：一键暂停全部自动执行 |
| GET | `/api/operations/zt-status` | 收银系统连通性 + 凭据状态 + 当前执行模式 |
| GET | `/api/operations/scan-preview` | 巡检预览（只读，不建任务） |
| POST | `/api/operations/scan` | **智能巡检**：扫库存/订单/咨询 → 生成待办（按护栏决定自动或待审） |
| GET | `/api/operations/tasks` | 任务列表（按状态 / 动作筛选） |
| GET | `/api/operations/tasks/{id}` | 任务详情（含护栏逐条判定 + 审计轨迹） |
| POST | `/api/operations/tasks` | 手工创建任务 |
| PATCH | `/api/operations/tasks/{id}` | 待审批阶段补充参数（如发货填运单号） |
| POST | `/api/operations/tasks/{id}/approve` | **批准**（可同时执行） |
| POST | `/api/operations/tasks/{id}/reject` | 驳回 |
| POST | `/api/operations/tasks/{id}/cancel` | 撤销 |
| POST | `/api/operations/tasks/{id}/execute` | 执行已批准任务 |
| GET | `/api/operations/audit` | 审计日志（谁、何时、做了什么） |
| GET | `/api/operations/stats` | 任务状态统计 + 今日自动执行次数 |

### 自动运营（无人值守）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/operations/auto-pilot` | 自动运营状态：是否开启、间隔、**下一轮时间**、最近 10 轮记录 |
| POST | `/api/operations/auto-pilot/run` | **立即跑一轮**（不等定时器，用于验证策略是否生效） |

## 五、运营执行层：自主化分级与护栏

### 自主化分级（对应「前期问你，后期自己干」）

| 等级 | 名称 | 行为 |
|---|---|---|
| `L0` | 仅建议 | 只生成待办，绝不执行 —— 纯参谋模式 |
| `L1` | 人工审批 | 每个动作都要人工点「批准」（**默认，最稳**） |
| `L2` | 限额内自动 | 白名单动作在限额与配额内自动执行，超出自动转人工 |
| `L3` | 白名单全自动 | 白名单动作只要过护栏就自动执行，适合成熟期 |

### 一条铁律：只有「可逆且不涉资金」的动作才允许自动化

| 动作 | 目标 | 风险 | 可逆 | 涉资金 | 能否自动 |
|---|---|---|---|---|---|
| 📦 `restock` 补充库存 | 商品 | 中 | ✅ | ❌ | ✅ 可进入自动评估 |
| 🚚 `ship` 订单发货 | 订单 | 中 | ❌ | ❌ | ⛔ 必须人工 |
| ↩️ `return` 退货处理 | 订单 | 高 | ❌ | ✅ | ⛔ 必须人工 |
| 🚫 `cancel` 取消订单 | 订单 | 高 | ❌ | ❌ | ⛔ 必须人工 |

> 发货/退货/取消**在任何等级下都必须人工审批** —— 这条规则写死在 `ActionSpec.auto_eligible`，
> 策略无法绕过。补库存之所以可以自动化，是因为库存可再次调整（可逆），且限额可控制其金额敞口。

### 七道护栏（逐条判定，全部通过才允许自动执行）

1. **急停开关** —— 开启后一切转人工（最高优先级）
2. **自主等级** —— L0 只建议
3. **动作性质** —— 不可逆 / 涉资金 → 永久人工
4. **动作白名单** —— 不在名单内转人工
5. **等级策略** —— L1 一律人工
6. **目标白名单** —— 限定试点范围（空 = 不限制）
7. **限额 + 每日配额** —— 超量或超次数转人工

每条判定都连同「通过/拦截 + 原因」一起落库，前端原样展示，让「AI 为什么没敢自己动手」完全可解释。

### 安全设计

| 机制 | 说明 |
|---|---|
| **幂等键** | 只取业务标识字段（如 `product_id+adjust`、`order_no`），**不含备注文本** |
| **在途去重** | 同一动作+目标若已有「待审批/已批准」任务，不重复建单 |
| **重复执行拦截** | 同一动作+目标在 10 分钟内被重复执行 → 判定误双击并拦截（时间窗，不挡合法重复业务） |
| **全量审计** | `action_audit_log` 只追加：创建/批准/驳回/执行/失败/改策略全部留痕 |
| **演练模式** | `dry_run` 只生成「将要发起的请求」不落地，适合演练与演示 |

## 六、自动运营（无人值守）——「后期成熟了，自己运营就行」

前面第五节解决的是「AI 想办事时，怎么保证它不乱来」；本节解决的是**「不用人喊，它自己也去办」**。

开启后，服务进程内会有一个轻量调度循环，按设定间隔自动跑一轮完整闭环：

```
定时到点 → 巡检（库存/订单/咨询）→ 按七道护栏建单 → 该自动的自动执行 → 转人工的进审批队列 → 留一条运行记录
```

人要做的事，从「每天点巡检」变成「有空看一眼报告、处理剩下需要人拍板的那几条」。

### 开关与节奏

| 策略项 | 默认 | 说明 |
|---|---|---|
| `autopilot_enabled` | `off` | 自动运营总开关。**默认关闭**，必须由人在页面上显式打开 |
| `autopilot_interval_minutes` | `30` | 巡检间隔，建议 30~120 分钟（数据变化没那么快，太频繁没意义） |

### 三条安全设计

1. **默认关闭** —— 不与既有行为打架。升级部署后若没人去开，系统行为和以前完全一样。
2. **不新增任何权限** —— 自动运营只是「替人按下巡检按钮」，能自动执行什么**完全由自主化策略与七道护栏决定**。
   把 `autopilot_enabled` 打开 ≠ 放开一切：发货/退货/取消在任何情况下都不会被自动执行。
3. **绝不拖垮服务** —— 循环体整体 try/except（任何异常只打印不抛出，循环不会退出）；
   数据库操作走 `asyncio.to_thread`，不阻塞事件循环；`TICK_SECONDS=60`，即每 60 秒才醒一次判断是否到点。

### 运维总闸

环境变量 `AUTOPILOT_DISABLED=1`（或 `true`/`on`/`yes`）可**强制不启动调度器**，
页面上的开关也随之失效。用于排障、迁移、或不希望容器后台跑定时任务的场景。

### 可观测性

| 观测点 | 位置 |
|---|---|
| 是否开启 / 间隔 / **下一轮时间** | 前端「运营任务中心」顶部状态条 |
| 每一轮干了什么 | 前端「🤖 自动运营记录」面板（触发方式 / 巡检结果 / 新建 / 自动执行 / 转人工） |
| 原始记录 | `autopilot_runs` 表（含本轮扫到的候选 JSON，便于复盘） |
| 进程日志 | `[autopilot] 调度器就绪…` / `[autopilot] 第 N 轮完成：…` / `[autopilot] 本轮异常已忽略：…` |

## 七、Agent 工具（LangChain）

| 工具 | 作用 |
|---|---|
| `get_sales_ranking` | 查销量排行 → 定主推商品 |
| `get_low_stock_products` | 查库存预警 → 避开缺货品 |
| `search_knowledge` | 检索企业知识库 → 取卖点素材 |
| `get_recent_customer_questions` | 看客户近期咨询 → 找营销切入点 |

**防幻觉设计**：所有数据必须由工具提供，系统提示明确禁止编造销量/价格/库存，也不允许承诺
「百分百上鱼」这类不可验证效果。

## 八、端到端验证结果（实测）

**营销侧**
| 环节 | 结果 |
|---|---|
| 只读连通 agent_db | ✅ 读到真实数据：订单 14 单、GMV ¥7036、钱包余额 ¥900、库存预警 3 项 |
| 销量 Top5 | ✅ 速攻2号 100件 / 钓鱼王 30件 / 九一八 10件 / 红虫颗粒 6件 |
| Agent 生成文案 | ✅ 真实调工具后产出朋友圈/社群文案，数据与库一致，无编造 |
| 草稿闭环 | ✅ 生成 → 落库「待审核」→ 审核「已通过」→ 统计正确流转 |
| 前端链路 | ✅ 页面 200 + 经 Vite 代理成功拉到后端真实数据 |

**运营执行层（本轮新增，全部为真实调用实测）**
| 用例 | 结果 |
|---|---|
| 巡检生成待办 | ✅ 扫出 3 个库存预警商品 → 生成 3 条补货待办（L1 下全部转人工） |
| **真实补货执行** | ✅ 批准后经 ZT-agent 接口成功补货，ZT 侧真实库存 `0 → 5`，复查一致（随后原路退回，净变化为零） |
| 真实错误处理 | ✅ 对不存在订单发起发货 → ZT 侧返回「订单不存在」，任务标记执行失败，不产生任何数据变更 |
| 高风险护栏 | ✅ 把等级调到最宽松的 L3，发货仍被判为「必须人工审批」 |
| 急停护栏 | ✅ 开启急停后，连可自动化的补货也转为待审批 |
| 限额护栏 | ✅ L2 下补货 33 件 > 上限 20 件 → 自动转人工 |
| 幂等（在途去重） | ✅ 同商品同数量、**仅备注不同**，命中同一张在途单 |
| 幂等（重复执行） | ✅ 10 分钟内重复执行被拦截，库存未再变化 |
| 审计留痕 | ✅ 创建/批准/执行/失败/改策略全部入库可查 |

**自动运营（无人值守，本轮新增，全部为真实调用实测）**
| 用例 | 结果 |
|---|---|
| 调度器启动 | ✅ 启动日志出现 `[autopilot] 调度器就绪，每 60s 检查一次` |
| **定时自动触发** | ✅ 开启后无需任何人工操作，到点自动产生 `trigger=timer` 的运行记录 |
| 立即执行一轮 | ✅ `POST /auto-pilot/run` 返回本轮巡检结果，写入 `autopilot_runs` |
| **无人值守真实自动执行** | ✅ L2 + 白名单商品 5 + 限额内 → 自动建单并**自动执行**，ZT 侧真实库存 `0 → 100`，任务标记「🤖 系统自动执行」（随后原路回滚，净变化为零） |
| 护栏不被绕过 | ✅ 同一轮里，白名单外的商品仍只生成待审批任务，不自动执行 |
| 急停对自动运营同样生效 | ✅ 急停开启后跑一轮：**只建单、零自动执行**，目标商品库存未发生任何变化 |
| 默认关闭 | ✅ 未开启时服务行为与升级前完全一致（只在你点巡检时才工作） |

> 说明：以上自动执行验证完成后已将库存**原路回滚到验证前数值**，并核对全部 6 个商品的库存、
> 预留量、预警线与订单状态与验证前**逐字段一致**，不留测试残留。

## 九、设计约束

| 约束 | 说明 |
|---|---|
| **只读业务表** | 对 `orders` / `products` / `inventory` 等只 SELECT，`db.py` 内置守卫拦截任何写语句（运行时硬约束，不是口头约定） |
| **写只走自管表** | 只写本服务新建的表（草稿 / 任务单 / 审计 / 策略） |
| **改业务走 ZT-agent 接口** | 需变更库存订单时，调用 ZT-agent 自身管理接口，由其保障事务与业务规则 |
| **不改 ZT-agent** | ZT-agent 零代码改动，可独立 `git pull && docker compose up` |
| **人工兜底** | 文案必须审核才发布；不可逆/涉资金动作必须人工审批 |

## 十、配置项

完整模板见 [`.env.example`](./.env.example)（复制为 `.env`，已被 `.gitignore` 忽略）。

| 变量 | 说明 |
|---|---|
| `DB_*` | 复用 ZT-agent 的 MySQL（只读） |
| `DEEPSEEK_API_KEY` | 与 ZT-agent 共用 |
| `ZT_AGENT_BASE_URL` | ZT-agent 地址，本地默认 `http://127.0.0.1:8000` |
| `ZT_STAFF_USER` / `ZT_STAFF_PASSWORD` | 调用管理接口用的 staff 账号（换取 JWT） |
| `MKT_EXECUTE_MODE` | `live` 真实执行 / `dry_run` 演练（页面上也可随时切换） |
| `ZT_AGENT_TIMEOUT` | 调用超时秒数 |
| `AUTOPILOT_DISABLED` | 留空=调度器正常随服务启动；`1`/`true`/`on`/`yes`=**强制不启动**（运维总闸） |

> 自动运营开关（`autopilot_enabled`）与间隔（`autopilot_interval_minutes`）属于**策略项**，
> 存在数据库 `autonomy_settings` 表里、可在页面上随时改，**不通过环境变量配置**。

> **已为营销 Agent 单独建了机器人账号 `mkt_bot`**（不是复用超级管理员）：`is_staff=True` / `is_superuser=False`，
> 最小权限、可随时在 ZT-agent 后台停用，且 ZT-agent 侧的审计日志能区分「人操作（admin）」与「Agent 操作（mkt_bot）」。
> 建号脚本在 ZT-agent 仓库：`python create_staff_bot.py`（幂等，重复执行只重置为最小权限）。

## 十一、踩坑记录

1. **uv 在中文路径下卡死** → venv 建到无中文路径（`D:/TYKKKKKK/.venvs/marketing-agent`）。
2. **PyPI 官方源极慢**（5 分钟无果）→ 用清华源 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`，29 秒装完。
3. **服务运行中装依赖会失败**（`orjson` 拒绝访问 / os error 5）→ 先停服务释放 venv 文件锁，再装。
4. **langchain 1.x 移除了旧 API**（`AgentExecutor` / `create_tool_calling_agent`）→ 改用官方新 API `langchain.agents.create_agent`。
5. **Git Bash 下 `taskkill //F` 无效**（`//` 被当路径）→ 用 `MSYS_NO_PATHCONV=1 taskkill /F /PID <pid>`。
6. **Vite 默认只监听 `[::1]`**，`127.0.0.1` 访问不到 → `vite.config.js` 显式 `host: '127.0.0.1'`。
7. **`.env` 位置不一致导致凭据读不到** → `db.py` 原本只找 `backend/.env`，而项目级配置在上一级目录；
   已统一为 `backend/.env → marketing-agent/.env → ZT-agent/.env` 三级回落（两个模块用同一套顺序）。
8. **配置在模块导入时就固化** → `zt_client` 若在 `.env` 加载前实例化，会读到空凭据；已改为懒加载属性。
9. **幂等键混入自由文本会失效** → 早期把 `reason` 备注算进幂等键，导致「同商品同数量、只改备注」就能绕过防重复；
   已改为只取业务标识字段（`idem_fields`）。
10. **`.env` 里写 `K=`（空值）会绕过 `os.getenv(k, default)`** → 统一用 `os.getenv(k) or default`。
11. **自动运营不新增权限**：`autopilot_enabled=on` 只是「让系统自己去按下巡检按钮」，
    能自动执行什么仍完全由自主化策略 + 七道护栏决定。**不要把开关当成权限开关**。
12. **`uv` 建的 venv 没有 pip**（`No module named pip`）→ 装依赖要用
    `uv pip install --python <venv>/Scripts/python.exe -r requirements.txt`，并且**遵守 requirements 的版本约束**
    （`html-to-docx` 要求 `lxml>=5.2,<6`，直接装 lxml 6.x 会出问题）。
