"""
营销自动化 Agent —— LangChain 编排层
──────────────────────────────────────
· 模型：DeepSeek（deepseek-chat），与 ZT-agent 共用同一 API Key（同一份 .env）
· 工具：marketing_tools 中的 4 个只读工具
· 模式：LangChain 1.x `create_agent`（内部基于 langgraph）——
        LLM 自主决定调用哪些工具、调用几次，形成「查数据 → 写文案」的多步工作流

与 ZT-agent 的分工（面试叙事点）：
  ZT-agent   = 手写 ReAct 循环 + Function Calling，吃透 Agent 内核原理
  本服务     = 用 LangChain 把多步营销工作流工程化，验证框架选型与工程化能力
  两者共用同一份数据与模型 → 构成 L5 多智能体能力闭环

注：LangChain 1.x 已移除旧的 AgentExecutor / create_tool_calling_agent，
    本项目采用官方新 API `langchain.agents.create_agent`。
"""
from __future__ import annotations

import os

import db  # noqa: F401  —— 复用其 .env 加载逻辑（含 ZT-agent 的 DEEPSEEK_API_KEY）
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from marketing_tools import MARKETING_TOOLS

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

SYSTEM_PROMPT = """你是中渔天下（渔具企业）的资深营销策划，负责产出可直接投放的营销文案。

工作流程（必须先查数据，再写文案）：
1. 查销量排行 → 确定主推商品
2. 查库存预警 → 避开缺货商品（缺货品绝不可主推）
3. 需要产品卖点/钓法知识时，检索企业知识库
4. 可选：查看客户近期咨询，找到客户真正关心的点

硬性要求：
- 文案中出现的所有数据（销量、价格、库存）必须来自工具返回，严禁编造。
- 不承诺工具数据之外的效果（例如"百分百上鱼""保证爆护"）。
- 文案要具体、有画面感、符合中国钓鱼人的语言习惯。
- 直接输出文案正文，不要输出你的推理过程或工具调用说明。"""


def build_agent():
    """构建 LangChain Agent（1.x create_agent）"""
    llm = ChatOpenAI(
        model=DEEPSEEK_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=DEEPSEEK_API_KEY or "missing-key",
        temperature=0.7,
        timeout=120,
        max_retries=2,
    )
    return create_agent(
        model=llm,
        tools=MARKETING_TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )


def _extract_text(message) -> str:
    """从最终 AIMessage 中提取纯文本（兼容 content 为分段列表的情况）"""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text", ""))
            else:
                parts.append(str(part))
        content = "".join(parts)
    return str(content or "").strip()


def generate_campaign(brief: str, channel: str = "朋友圈", tone: str = "促销", extra: str = "") -> str:
    """根据运营需求生成营销文案（Agent 会自行调用工具查数据）。

    :param brief: 运营需求，如「推一下蓝鲫X5，冲一波秋季销量」
    :param channel: 投放渠道（朋友圈 / 社群 / 公众号 / 短信 / 直播口播）
    :param tone: 文案语气（促销 / 专业 / 温情 / 幽默）
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY（请在 .env 中设置）")

    task = (
        f"投放渠道：{channel}\n"
        f"文案语气：{tone}\n"
        f"运营需求：{brief}\n"
        f"{('补充要求：' + extra) if extra else ''}\n\n"
        f"请先调用工具查询必要的数据，然后直接输出一段适配「{channel}」、语气为「{tone}」的营销文案。"
    )
    agent = build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": task}]})
    messages = (result or {}).get("messages") or []
    if not messages:
        return ""
    return _extract_text(messages[-1])


def is_llm_ready() -> bool:
    """LLM 是否已配置（供健康检查用）"""
    return bool(DEEPSEEK_API_KEY)
