"""Exercise the real Anthropic SDK code path against a local fake server (no API key, no cost)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from shorts_factory.config import LLMCfg
from shorts_factory.content.llm import FALLBACK_BETA, AnthropicLLM, LLMError
from shorts_factory.content.schemas import Critique

CRITIQUE = {"hook_score": 9, "retention_score": 8, "clarity_score": 8, "payoff_score": 8,
            "originality_score": 7, "accuracy_risk": "low", "policy_risk": "low", "issues": [], "verdict": "publish"}


class _Fake(BaseHTTPRequestHandler):
    captured: list = []
    reply: dict = {}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _Fake.captured.append((dict(self.headers), body))
        out = json.dumps(_Fake.reply).encode()
        self.send_response(200)
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
    _Fake.captured = []
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
    assert AnthropicLLM(LLMCfg(fallbacks=False)).research("s", "1. claim") == "1 | OK"
    _, body = fake_api.captured[0]
    assert body["tools"][0]["type"] == "web_search_20260209"
    assert "fallbacks" not in body
