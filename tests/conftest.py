"""共享测试设施。

fixture 约定（供后续工具模块测试复用）：

1. `load_dotenv()` 在本模块 import 时执行，真实 API 测试（@pytest.mark.real）直接读
   环境变量 JV_API_KEY；绝不在任何断言/日志中输出 key。
2. `make_decide_response(questions)` 构造合法的 /api/v1/decide 响应工厂。
3. `MockJevAPI`：可编排的 fake jev HTTP 端点。用 `push(status, body)` 排队响应、
   `push_error(exc)` 排队传输层异常（如 httpx.ConnectError），`requests` 记录所有
   收到的请求（用于断言重试次数）。
4. **JevClient 注入点约定**：每个工具模块在模块级定义 `get_client() -> JevClient`
   惰性单例；测试用 `patched_tool_client(client)` fixture 将其 monkeypatch 为
   走 MockTransport 的 client，从而实现不触网的端到端工具测试。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from dotenv import load_dotenv

from jev_harness_tool.core.client import JevClient
from jev_harness_tool.core.config import Settings

load_dotenv()

FAKE_API_KEY = "jv_live_test_fake_key"


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        api_key=FAKE_API_KEY,
        base_url="https://jev.test",
        model="jev-test",
        timeout_s=5.0,
        max_retries=3,
        backoff_base_s=0.0,
    )
    base.update(overrides)
    return Settings(**base)


def make_decide_response(
    questions: dict[str, Any],
    *,
    model: str = "jev-test",
    input_tokens: int = 42,
) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for name, q in questions.items():
        qtype = q.get("type")
        if qtype == "noul":
            answers[name] = {"type": "noul", "noul": 0.9}
        elif qtype == "choice":
            keys = list(q["criteria"].keys())
            probs = {k: 0.0 for k in keys}
            probs[keys[0]] = 0.9
            for k in keys[1:]:
                probs[k] = round(0.1 / (len(keys) - 1), 6)
            answers[name] = {
                "type": "choice",
                "choice": keys[0],
                "confidence": 0.9,
                "probabilities": probs,
            }
        elif qtype == "score":
            answers[name] = {
                "type": "score",
                "score": 1.0,
                "confidence": 1.0,
                "probabilities": {"1": 1.0},
            }
        else:
            raise ValueError(f"unknown question type in fixture: {qtype!r}")
    return {
        "model": model,
        "answers": answers,
        "usage": {
            "input_tokens": input_tokens,
            "cost_usd": 0.00001,
            "credits_remaining_usd": 4.99,
        },
    }


class MockJevAPI:
    """Scriptable fake jev HTTP endpoint backed by httpx.MockTransport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._script: list[Any] = []

    def push(self, status: int = 200, body: Any = None) -> "MockJevAPI":
        self._script.append((status, body))
        return self

    def push_error(self, exc: Exception) -> "MockJevAPI":
        self._script.append(exc)
        return self

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(f"MockJevAPI: unexpected request #{len(self.requests)}")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body, request=request)


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def mock_api() -> MockJevAPI:
    return MockJevAPI()


@pytest.fixture
def make_client(mock_api):
    def _make(**overrides: Any) -> JevClient:
        return JevClient(
            make_settings(**overrides),
            transport=mock_api.transport,
            sleep=_no_sleep,
        )

    return _make


@pytest.fixture
def patched_tool_client(monkeypatch):
    """把 mock client 注入工具模块的 get_client()。默认注入 tools.decide，
    后续工具模块传 module 参数即可复用同一约定。"""

    def _install(client: JevClient, module: str = "jev_harness_tool.tools.decide") -> JevClient:
        monkeypatch.setattr(f"{module}.get_client", lambda: client)
        return client

    return _install
