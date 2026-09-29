"""tool_risk_gate —— 工具调用语义风险门控。

薄封装 jev 现成端点 POST /api/v1/agent/risk：问题措辞固化在 jev 服务端，
本模块负责参数校验、defensive 截断、token 预算预检、响应双层校验、
verdict 二次映射（risk 阈值覆盖 / confidence 升档 / 截断降权）与 fail-closed。
详设：docs/design/tools/tool_risk_gate.md
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..core.client import (
    JevAuthError,
    JevClient,
    JevError,
    JevNetworkError,
    JevPaymentError,
    JevProtocolError,
    JevUpstreamError,
    JevValidationError,
    get_shared_client,
)
from ..core.tokens import estimate_tokens

RISK_PATH = "/api/v1/agent/risk"

GOAL_MAX_CHARS = 4_000
CONTEXT_MAX_CHARS = 4_000
TOOL_MAX_CHARS = 200
# 32k 单问预算留 2k 安全边际；超限不发请求（详设 §5.1）
TOKEN_BUDGET = 30_000

DEFAULT_RISK_ALLOW_MAX = 0.3
DEFAULT_RISK_BLOCK_MIN = 0.7
DEFAULT_CONFIDENCE_MIN = 0.4
DEFAULT_ARGUMENTS_MAX_CHARS = 16_000

_ACTIONS = ("allow", "confirm", "block")
_ACTION_LEVEL = {"allow": 0, "confirm": 1, "block": 2}
_VERDICTS = ("allow", "review", "block")

def get_client() -> JevClient:
    """工具获取 JevClient 的唯一入口。测试通过 monkeypatch 本函数注入 mock client。

    委托 core 的循环感知共享单例（get_shared_client），事件循环更换时自动重建。
    """
    return get_shared_client()


def _is_probability(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0


def _validate_risk_payload(data: Any) -> str | None:
    """现成端点响应的 transport 层校验（详设 §3.5）。返回 None 或失败原因。"""
    if not isinstance(data, dict):
        return "response is not a JSON object"
    if data.get("action") not in _ACTIONS:
        return "`action` missing or not one of allow/confirm/block"
    if not _is_probability(data.get("risk")):
        return "`risk` missing or outside [0,1]"
    categories = data.get("categories")
    if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
        return "`categories` missing or not an array of strings"
    confidence = data.get("confidence")
    if confidence is not None and not _is_probability(confidence):
        return "`confidence` outside [0,1]"
    return None


def _extract_usage(data: dict[str, Any]) -> dict[str, Any] | None:
    """usage 原样透传；缺失/畸形时返回 None（不因此判 invalid_response）。"""
    usage = data.get("usage")
    if (
        isinstance(usage, dict)
        and isinstance(usage.get("input_tokens"), (int, float))
        and not isinstance(usage.get("input_tokens"), bool)
    ):
        return usage
    return None


def _compute_verdict(
    action: str,
    risk: float,
    confidence: float | None,
    truncated: bool,
    risk_allow_max: float,
    risk_block_min: float,
    confidence_min: float,
) -> str:
    """详设 §4.3：action 基线与 risk 覆盖取较严格者，confidence 不足 / 截断向严格方向升档。"""
    level = _ACTION_LEVEL[action]
    if risk >= risk_block_min:
        level = max(level, 2)
    elif risk >= risk_allow_max:
        level = max(level, 1)
    if confidence is None or confidence < confidence_min:
        level = max(level, 1)
    if truncated:
        level = max(level, 1)  # 截断输入永不自动放行（原则 12）
    return _VERDICTS[level]


def _error_dict(exc: JevError) -> dict[str, Any]:
    if isinstance(exc, JevAuthError):
        code = "auth_error" if exc.status_code == 401 else "account_inactive"
    elif isinstance(exc, JevPaymentError):
        code = "insufficient_credits"
    elif isinstance(exc, JevValidationError):
        code = "bad_request"
    elif isinstance(exc, JevUpstreamError):
        code = "upstream_unavailable"
    elif isinstance(exc, JevNetworkError):
        code = "timeout" if "Timeout" in str(exc) else "network_error"
    else:
        code = "jev_error"
    return {"code": code, "message": str(exc), "retryable": False}


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="tool_risk_gate")
    async def tool_risk_gate(
        goal: str,
        tool: str,
        arguments: str,
        context: str | None = None,
        risk_allow_max: float = DEFAULT_RISK_ALLOW_MAX,
        risk_block_min: float = DEFAULT_RISK_BLOCK_MIN,
        confidence_min: float = DEFAULT_CONFIDENCE_MIN,
        arguments_max_chars: int = DEFAULT_ARGUMENTS_MAX_CHARS,
    ) -> dict[str, Any]:
        """工具调用前的语义风险门控：返回 verdict（allow/review/block/invalid_response）。

        在工具执行前 hook 中调用。本工具只建议不执行（原则 14）；`arguments` 按
        外部内容对待——它可能含注入文本，会作为证据传给 jev 评估而非指令执行，
        调用方对高危来源内容应先行清洗。误放行代价远高于误拦截，故默认阈值偏保守：
        risk < risk_allow_max 才可能 allow，risk ≥ risk_block_min 强制 block，
        confidence 低于 confidence_min 或 arguments 被截断时 verdict 向严格方向升档。
        HTTP 层失败不输出 verdict，返回结构化 error，降级策略由调用方决定。
        """
        # ---- 参数校验（失败不发请求）----
        if not goal or len(goal) > GOAL_MAX_CHARS:
            raise ToolError(f"goal must be 1..{GOAL_MAX_CHARS} chars (got {len(goal)})")
        if not tool or len(tool) > TOOL_MAX_CHARS:
            raise ToolError(f"tool must be 1..{TOOL_MAX_CHARS} chars (got {len(tool)})")
        if context is not None and len(context) > CONTEXT_MAX_CHARS:
            raise ToolError(f"context must be <= {CONTEXT_MAX_CHARS} chars (got {len(context)})")
        if arguments_max_chars < 1:
            raise ToolError("arguments_max_chars must be >= 1")
        if not (
            _is_probability(risk_allow_max)
            and _is_probability(risk_block_min)
            and _is_probability(confidence_min)
            and risk_allow_max < risk_block_min
        ):
            raise ToolError(
                "thresholds must satisfy 0 <= risk_allow_max < risk_block_min <= 1 "
                "and 0 <= confidence_min <= 1"
            )

        thresholds = {
            "risk_allow_max": risk_allow_max,
            "risk_block_min": risk_block_min,
            "confidence_min": confidence_min,
        }
        out: dict[str, Any] = {
            "verdict": None,
            "jev_action": None,
            "risk": None,
            "categories": [],
            "confidence": None,
            "truncated": False,
            "thresholds": thresholds,
            "model": None,
            "usage": None,
            "error": None,
        }

        # ---- arguments 防御性截断（头部保留）----
        if len(arguments) > arguments_max_chars:
            arguments = arguments[:arguments_max_chars]
            out["truncated"] = True

        body: dict[str, Any] = {"goal": goal, "tool": tool, "arguments": arguments}
        if context is not None:
            body["context"] = context

        # ---- token 预算预检（原则 9；超限不发请求）----
        if estimate_tokens(body) > TOKEN_BUDGET:
            out["error"] = {
                "code": "input_too_large",
                "message": "estimated input tokens exceed the 30k budget; shrink goal/context/arguments_max_chars",
                "retryable": False,
            }
            return out

        # ---- 调 jev 现成端点 ----
        try:
            data = await get_client().post_readymade(RISK_PATH, body)
        except JevProtocolError as exc:
            # 200 但响应不是合法 JSON 对象 / usage 畸形 → fail-closed 为 invalid_response
            out["verdict"] = "invalid_response"
            out["error"] = {"code": "invalid_response", "message": str(exc), "retryable": False}
            return out
        except JevError as exc:
            out["error"] = _error_dict(exc)
            return out

        out["usage"] = _extract_usage(data) if isinstance(data, dict) else None
        model = data.get("model") if isinstance(data, dict) else None
        if isinstance(model, str):
            out["model"] = model

        # ---- 响应校验，fail-closed（原则 12/13）----
        reason = _validate_risk_payload(data)
        if reason is not None:
            out["verdict"] = "invalid_response"
            out["error"] = {"code": "invalid_response", "message": reason, "retryable": False}
            return out

        out["jev_action"] = data["action"]
        out["risk"] = float(data["risk"])
        out["categories"] = list(data["categories"])
        confidence = data.get("confidence")
        out["confidence"] = float(confidence) if confidence is not None else None
        out["verdict"] = _compute_verdict(
            data["action"],
            out["risk"],
            out["confidence"],
            out["truncated"],
            risk_allow_max,
            risk_block_min,
            confidence_min,
        )
        return out
