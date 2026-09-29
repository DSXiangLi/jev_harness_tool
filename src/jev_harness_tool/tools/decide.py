from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..core.client import JevClient, JevError, get_shared_client


def get_client() -> JevClient:
    """工具获取 JevClient 的唯一入口。测试通过 monkeypatch 本函数注入 mock client。

    委托 core 的循环感知共享单例（get_shared_client），事件循环更换时自动重建。
    """
    return get_shared_client()


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="jev_decide")
    async def jev_decide(
        state: str | dict | list,
        questions: dict,
        model: str | None = None,
    ) -> dict[str, Any]:
        """通用决策原语：一个 state + 一组带类型问题（choice/score/noul）→ 校准 answers。

        questions 原样透传给 jev /api/v1/decide；响应经 transport 层 envelope 校验，
        返回 {model, answers, usage}，usage 含 input_tokens / cost_usd。
        """
        try:
            result = await get_client().decide(state, questions, model=model)
        except JevError as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc
        return {
            "model": result.model,
            "answers": result.answers,
            "usage": result.usage,
        }
