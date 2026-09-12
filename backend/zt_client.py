"""
ZT-agent 执行器 —— 营销 Agent 的「手脚」
════════════════════════════════════════════════════════════
设计边界（很重要，是整个双 Agent 架构的地基）：

  营销 Agent **永远不直接写** ZT-agent 的业务表（orders / inventory ...）。
  它只能以「一个后台操作员」的身份，调用 ZT-agent **自己已经提供的**
  管理接口（HTTP + JWT），由 ZT-agent 去做它自己的库存/订单状态机变更。

这样带来三个好处：
  1. 数据一致性：库存扣减、订单状态机、钱包记账仍由 ZT-agent 的事务保障，
     营销侧不可能绕过业务规则写出脏数据；
  2. 零侵入：ZT-agent 一行代码都不改，永远能独立运行；
  3. 可审计：ZT-agent 侧的日志/流水同样记录这次操作，责任链完整。

两种执行模式（由 MKT_EXECUTE_MODE 控制）：
  · live    —— 真实调用，产生真实业务变更；
  · dry_run —— 只拼出「将要发起的请求」，不落地，用于演练与演示。

凭据：ZT_STAFF_USER / ZT_STAFF_PASSWORD（建议在 ZT-agent 后台为营销 Agent
单独建一个 staff 机器人账号，最小权限、可随时停用，比复用 admin 更安全）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# 自身加载 .env（不依赖 import 顺序）：
#   backend/.env（后端目录）→ marketing-agent/.env（项目根，docker compose 读这份）
#   → ZT-agent/.env（共享同一份 DB 凭据与模型密钥的兜底）
_BASE_DIR = Path(__file__).resolve().parent
_ENV_CANDIDATES = [
    _BASE_DIR / ".env",
    _BASE_DIR.parent / ".env",
    _BASE_DIR.parent.parent / "ZT-agent" / ".env",
]
for _p in _ENV_CANDIDATES:
    if _p.exists():
        load_dotenv(_p, override=False)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"   # ZT-agent 本机开发服务
DEFAULT_TIMEOUT = 20                          # 秒
TOKEN_SAFETY_WINDOW = 60                      # token 剩余不足 60s 视为过期，提前重取


class ZTAgentError(RuntimeError):
    """ZT-agent 调用失败（网络/鉴权/业务拒绝），message 面向运营人员可读"""


def _cfg(key: str, default: str = "") -> str:
    """读取配置。

    注意：本项目踩过 `os.getenv(k, default)` 的坑 —— 当 .env 里写成 `K=`（空串）时，
    getenv 会返回空字符串而**不会**回退到 default。这里统一用 `or` 兜底。
    """
    return (os.getenv(key) or "").strip() or default


class ZTAgentClient:
    """ZT-agent 管理接口的薄封装（登录 / 带 token 调用 / 业务动作语义化）"""

    def __init__(self) -> None:
        self._token: str = ""
        self._token_deadline: float = 0.0
        self.last_login_error: str = ""
        # 由策略层（operations.execute_task）按当前 policy 注入；
        # 为空时回落环境变量 MKT_EXECUTE_MODE —— 保证「页面上改的策略」立即生效。
        self.mode_override: str = ""

    # ── 配置（懒读取：改 .env 后重启即生效，不受 import 顺序影响）──────
    @property
    def base_url(self) -> str:
        return _cfg("ZT_AGENT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    @property
    def username(self) -> str:
        return _cfg("ZT_STAFF_USER")

    @property
    def password(self) -> str:
        return _cfg("ZT_STAFF_PASSWORD")

    @property
    def timeout(self) -> int:
        try:
            return int(_cfg("ZT_AGENT_TIMEOUT", str(DEFAULT_TIMEOUT)) or DEFAULT_TIMEOUT)
        except ValueError:
            return DEFAULT_TIMEOUT

    # ── 配置状态 ──────────────────────────────────────────────
    @property
    def mode(self) -> str:
        """live / dry_run"""
        m = (self.mode_override or _cfg("MKT_EXECUTE_MODE", "live")).lower()
        return m if m in ("live", "dry_run") else "live"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def credentials_ready(self) -> bool:
        return bool(self.username and self.password)

    # ── 底层 HTTP ─────────────────────────────────────────────
    def _raw(self, method: str, path: str, payload: dict | None = None,
             token: str = "") -> tuple[int, dict]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                try:
                    return resp.status, json.loads(body or b"{}")
                except json.JSONDecodeError:
                    return resp.status, {"raw": body[:500].decode("utf-8", "ignore")}
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, json.loads(body or b"{}")
            except json.JSONDecodeError:
                return e.code, {"raw": body[:500].decode("utf-8", "ignore")}
        except urllib.error.URLError as e:
            raise ZTAgentError(
                f"连不上 ZT-agent（{self.base_url}）：{e.reason}。请确认 ZT-agent 已启动。"
            ) from e
        except TimeoutError as e:
            raise ZTAgentError(f"调用 ZT-agent 超时（{self.timeout}s），请稍后重试。") from e

    def login(self, force: bool = False) -> str:
        """登录取 access token（带缓存，到期前自动续）"""
        if not self.credentials_ready():
            raise ZTAgentError(
                "未配置 ZT-agent 管理员凭据。请在 .env 设置 ZT_STAFF_USER / ZT_STAFF_PASSWORD"
                "（建议在 ZT-agent 后台为营销 Agent 建一个专用 staff 账号）。"
            )
        if not force and self._token and time.time() < self._token_deadline:
            return self._token

        status, res = self._raw(
            "POST", "/api/auth/login/",
            {"username": self.username, "password": self.password},
        )
        token = res.get("access") or (res.get("data") or {}).get("access") or ""
        if status != 200 or not token:
            msg = res.get("detail") or res.get("message") or "用户名或密码错误"
            self.last_login_error = str(msg)
            raise ZTAgentError(f"登录 ZT-agent 失败：{msg}")
        self._token = token
        # 默认 access 有效期 30 分钟，安全窗口内提前重取
        self._token_deadline = time.time() + 30 * 60 - TOKEN_SAFETY_WINDOW
        self.last_login_error = ""
        return token

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
        """带鉴权调用 ZT-agent 接口。

        返回统一结构：{"ok", "status", "message", "data", "dry_run"}
        - dry_run 模式不发起真实请求，只回显「将要执行的调用」，便于演练。
        """
        preview = {"method": method, "url": f"{self.base_url}{path}", "payload": payload or {}}
        if not self.is_live:
            return {
                "ok": True, "status": 0, "dry_run": True,
                "message": "演练模式：已生成调用请求，未真实执行",
                "data": {"preview": preview},
            }

        token = self.login()
        status, res = self._raw(method, path, payload, token=token)
        # token 过期 → 强制续期重试一次
        if status == 401:
            token = self.login(force=True)
            status, res = self._raw(method, path, payload, token=token)

        ok = status == 200 and bool(res.get("success", True)) and not res.get("detail")
        message = res.get("message") or res.get("detail") or ("执行成功" if ok else f"ZT-agent 返回 {status}")
        return {"ok": ok, "status": status, "message": str(message), "data": res, "dry_run": False}

    # ── 业务动作（语义化封装，与 ZT-agent 既有接口一一对应）────────
    def restock(self, product_id: int, adjust: int, reason: str = "营销侧智能补货") -> dict:
        """补货：PATCH /api/inventory/<product_id>/  {"adjust": +N}"""
        return self.call("PATCH", f"/api/inventory/{int(product_id)}/",
                         {"adjust": int(adjust), "reason": reason})

    def ship(self, order_no: str, ship_company: str, tracking_no: str) -> dict:
        """发货：POST /api/orders/<order_no>/ship/（ZT 侧扣减库存）"""
        return self.call("POST", f"/api/orders/{order_no}/ship/",
                         {"ship_company": ship_company, "tracking_no": tracking_no})

    def return_order(self, order_no: str, reason: str = "客户退货") -> dict:
        """退货：POST /api/orders/<order_no>/return/（ZT 侧库存回滚 + 钱包退款）"""
        return self.call("POST", f"/api/orders/{order_no}/return/",
                         {"cancel_reason": reason})

    def cancel_order(self, order_no: str, reason: str = "营销侧取消") -> dict:
        """取消：POST /api/orders/<order_no>/cancel/（释放预占库存）"""
        return self.call("POST", f"/api/orders/{order_no}/cancel/",
                         {"cancel_reason": reason})

    def coordinate(self, event: str, product_id: int, notice: str = "",
                   source: str = "marketing-agent") -> dict:
        """L5 编排：向 ZT-agent 接收钩子发送跨 Agent 协调指令。

        POST /api/coordination/inbound/
          {"event": "pause_product"|"resume_product"|"set_notice",
           "product_id": N, "customer_notice": "...", "source": "marketing-agent"}

        dry_run 模式只回显请求、不真实发送；真实发送时由 ZT-agent 写入
        product_coordination 覆盖表（绝不碰业务表）。
        """
        payload = {
            "event": event,
            "product_id": int(product_id),
            "source": source,
        }
        if notice:
            payload["customer_notice"] = notice
        return self.call("POST", "/api/coordination/inbound/", payload)

    # ── 连通性自检（供 /api/operations/zt-status 使用）──────────
    def status(self) -> dict:
        """不抛异常地汇报 ZT-agent 可达性 + 凭据状态（供运营页面展示）"""
        try:
            from operations import get_policy                      # 延迟导入，避免循环依赖
            from db import WriteSession

            _db = WriteSession()
            try:
                self.mode_override = get_policy(_db).get("execute_mode", "")
            finally:
                _db.close()
        except Exception:
            pass
        info = {
            "base_url": self.base_url,
            "mode": self.mode,
            "credentials_configured": self.credentials_ready(),
            "staff_user": self.username or "(未配置)",
            "reachable": False,
            "login_ok": False,
            "http_status": None,
            "message": "",
        }
        try:
            status, _ = self._raw("GET", "/healthz")
            info["reachable"] = status == 200
            info["http_status"] = status
        except ZTAgentError as e:
            info["message"] = str(e)
            return info
        if info["reachable"] and self.credentials_ready():
            try:
                self.login(force=True)
                info["login_ok"] = True
                info["message"] = "ZT-agent 可达，管理员凭据验证通过"
            except ZTAgentError as e:
                info["message"] = str(e)
        elif info["reachable"]:
            info["message"] = "ZT-agent 可达，但未配置管理员凭据（仅演练模式可用）"
        else:
            info["message"] = f"ZT-agent 不可达（HTTP {info['http_status']}）"
        return info


# 单例：token 在进程内复用，避免每次动作都登录
client = ZTAgentClient()
