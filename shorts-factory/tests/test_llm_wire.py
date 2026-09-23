"""Exercise the real Anthropic SDK code path against a local fake server (no API key, no cost)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from shorts_factory.config import LLMCfg
from shorts_factory.content.llm import FALLBACK_BETA, WORKSPACE_HINT, AnthropicLLM, LLMError
from shorts_factory.content.schemas import Critique

CRITIQUE = {"hook_score": 9, "retention_score": 8, "clarity_score": 8, "payoff_score": 8,
            "originality_score": 7, "accuracy_risk": "low", "policy_risk": "low", "issues": [], "verdict": "publish"}


class _Fake(BaseHTTPRequestHandler):
    captured: list = []
    reply: dict = {}
    status: int = 200

    def do_GET(self):  # noqa: N802
        _Fake.captured.append(({k.lower(): v for k, v in self.headers.items()}, None))
        self._send()

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _Fake.captured.append(({k.lower(): v for k, v in self.headers.items()}, body))
        self._send()

    def _send(self):
        out = json.dumps(_Fake.reply).encode()
        self.send_response(_Fake.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_api(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    _Fake.captured, _Fake.status = [], 200
    yield _Fake
    srv.shutdown()


def _message(text, stop="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 34}}


def test_structured_request_shape(fake_api):
    fake_api.reply = _message(json.dumps(CRITIQUE))
    llm = AnthropicLLM(LLMCfg())
    out = llm.structured("system prompt", "user prompt", Critique, effort="high")
    assert isinstance(out, Critique) and out.hook_score == 9
    headers, body = fake_api.captured[0]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in headers.get("anthropic-beta", "")
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert llm.usage["output_tokens"] == 34


def test_refusal_raises(fake_api):
    fake_api.reply = _message("", stop="refusal")
    with pytest.raises(LLMError):
        AnthropicLLM(LLMCfg()).structured("s", "u", Critique)


def test_research_uses_web_search_tool(fake_api):
    fake_api.reply = _message("1 | OK")
    assert AnthropicLLM(LLMCfg(fallbacks=False)).research("s", "1. claim", max_uses=3) == "1 | OK"
    _, body = fake_api.captured[0]
    assert body["tools"][0]["type"] == "web_search_20260209" and body["tools"][0]["max_uses"] == 3
    assert "fallbacks" not in body


def test_workspace_id_header(fake_api, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test123")
    fake_api.reply = _message(json.dumps(CRITIQUE))
    AnthropicLLM(LLMCfg()).structured("s", "u", Critique)
    headers, _ = fake_api.captured[0]
    assert headers["anthropic-workspace-id"] == "wrkspc_test123"


def test_no_workspace_header_by_default(fake_api):
    fake_api.reply = _message(json.dumps(CRITIQUE))
    AnthropicLLM(LLMCfg()).structured("s", "u", Critique)
    assert "anthropic-workspace-id" not in fake_api.captured[0][0]


def test_unscoped_key_error_is_explained(fake_api):
    fake_api.status = 400
    fake_api.reply = {"type": "error", "error": {"type": "invalid_request_error", "message": (
        "This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id "
        "header with the ID of the workspace to use. Add the header, or use an API key that is scoped to a "
        "workspace.")}}
    llm = AnthropicLLM(LLMCfg())
    with pytest.raises(LLMError, match="ANTHROPIC_WORKSPACE_ID"):
        llm.structured("s", "u", Critique)
    with pytest.raises(LLMError) as e:
        llm.ping()
    assert str(e.value) == WORKSPACE_HINT


def test_ping(fake_api):
    fake_api.reply = {"type": "model", "id": "claude-opus-5", "display_name": "Claude Opus 5",
                      "created_at": "2026-01-01T00:00:00Z"}
    assert AnthropicLLM(LLMCfg()).ping() == "claude-opus-5"
