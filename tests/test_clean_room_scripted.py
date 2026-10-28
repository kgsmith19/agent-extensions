"""RED: in-process scripted wire-protocol doubles for the clean-room benchmark (#91).

Contract under test (canary/benchmark/lanes/scripted.py, not written yet):

  ChatCompletionsDouble(script)   OpenAI chat completions (kilo). Same SSE as
                                  canary/kilo/scripted_model.py for the same script.
  ResponsesDouble(script)         OpenAI Responses API (codex). POST /v1/responses.
  MessagesDouble(script)          Anthropic Messages API (claude-code). POST /v1/messages.
  GenerateContentDouble(script)   Gemini (gemini-cli). POST /v1beta/models/{m}:streamGenerateContent?alt=sse.

Each double is a context manager bound to 127.0.0.1 port 0. It exposes
`.base_url` (scheme, host and port only, no path) and `.requests` (the parsed
JSON request bodies, in arrival order). The script is served one reply per
request, in order.

Hermetic: stdlib only, in-process HTTP, no CLI, no network beyond loopback.
"""

import argparse
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from canary.benchmark.lanes.scripted import (  # noqa: E402
    ChatCompletionsDouble,
    GenerateContentDouble,
    MessagesDouble,
    ResponsesDouble,
)
from canary.kilo import scripted_model as sm  # noqa: E402

SCRIPT = ["first reply", "second reply"]


# --- client helpers -------------------------------------------------------------------


def _request(base_url, method, path, body=None):
    """One HTTP exchange over loopback. Returns (status, content_type, body_bytes)."""
    parts = urlsplit(base_url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()
    finally:
        conn.close()


def _sse_events(data):
    """Split an SSE body into (event_name_or_None, payload_str) pairs."""
    events = []
    for block in data.decode("utf-8").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name, payload = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                payload.append(line[len("data:"):].strip())
        events.append((name, "\n".join(payload)))
    return events


def _json_payloads(data):
    """JSON objects carried in `data:` lines, skipping the [DONE] sentinel."""
    return [json.loads(p) for _, p in _sse_events(data) if p and p != "[DONE]"]


# --- per-protocol request bodies and extractors ----------------------------------------


def _chat_body(text="hi"):
    # include_usage asks for the trailing usage chunk; see test_chat_usage_chunk_*.
    return {"model": "m1", "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": text}]}


def _responses_body(text="hi"):
    return {"model": "m1", "stream": True, "input": text}


def _messages_body(text="hi"):
    return {"model": "m1", "stream": True, "max_tokens": 64,
            "messages": [{"role": "user", "content": text}]}


def _gemini_body(text="hi"):
    return {"contents": [{"role": "user", "parts": [{"text": text}]}]}


def _chat_text(data):
    return "".join(
        c["delta"].get("content", "")
        for chunk in _json_payloads(data) if chunk.get("choices")
        for c in chunk["choices"]
    )


def _responses_text(data):
    return "".join(
        json.loads(p)["delta"] for name, p in _sse_events(data)
        if name == "response.output_text.delta"
    )


def _messages_text(data):
    return "".join(
        json.loads(p)["delta"]["text"] for name, p in _sse_events(data)
        if name == "content_block_delta" and json.loads(p)["delta"]["type"] == "text_delta"
    )


def _gemini_text(data):
    return "".join(
        part["text"]
        for chunk in _json_payloads(data)
        for part in chunk["candidates"][0]["content"]["parts"]
        if "text" in part
    )


def _chat_usage(data):
    usage = [c["usage"] for c in _json_payloads(data) if c.get("usage")]
    return usage[-1]["prompt_tokens"], usage[-1]["completion_tokens"]


def _responses_usage(data):
    completed = [json.loads(p) for name, p in _sse_events(data) if name == "response.completed"]
    usage = completed[-1]["response"]["usage"]
    return usage["input_tokens"], usage["output_tokens"]


def _messages_usage(data):
    events = [(name, json.loads(p)) for name, p in _sse_events(data)]
    start = next(p for name, p in events if name == "message_start")
    delta = next(p for name, p in events if name == "message_delta")
    return start["message"]["usage"]["input_tokens"], delta["usage"]["output_tokens"]


def _gemini_usage(data):
    meta = _json_payloads(data)[-1]["usageMetadata"]
    return meta["promptTokenCount"], meta["candidatesTokenCount"]


PROTOCOLS = {
    "chat": (ChatCompletionsDouble, "/v1/chat/completions", _chat_body,
             _chat_text, _chat_usage),
    "responses": (ResponsesDouble, "/v1/responses", _responses_body,
                  _responses_text, _responses_usage),
    "messages": (MessagesDouble, "/v1/messages", _messages_body,
                 _messages_text, _messages_usage),
    "gemini": (GenerateContentDouble, "/v1beta/models/m1:streamGenerateContent?alt=sse",
               _gemini_body, _gemini_text, _gemini_usage),
}


@pytest.fixture(params=sorted(PROTOCOLS))
def protocol(request):
    return PROTOCOLS[request.param]


# --- every double: shared behaviour ---------------------------------------------------


def test_double_streams_scripted_text_in_order_across_two_requests(protocol):
    cls, path, body, text_of, _ = protocol
    with cls(SCRIPT) as double:
        status1, _, data1 = _request(double.base_url, "POST", path, body("one"))
        status2, _, data2 = _request(double.base_url, "POST", path, body("two"))
    assert status1 == 200
    assert status2 == 200
    assert text_of(data1) == "first reply"
    assert text_of(data2) == "second reply"


def test_double_serves_event_stream_content_type(protocol):
    cls, path, body, _, _ = protocol
    with cls(SCRIPT) as double:
        _, content_type, _ = _request(double.base_url, "POST", path, body())
    assert content_type.startswith("text/event-stream")


def test_double_records_every_request_body_in_order(protocol):
    cls, path, body, _, _ = protocol
    first, second = body("one"), body("two")
    with cls(SCRIPT) as double:
        _request(double.base_url, "POST", path, first)
        _request(double.base_url, "POST", path, second)
        recorded = list(double.requests)
    assert recorded == [first, second]


def test_double_returns_a_usage_block(protocol):
    cls, path, body, _, usage_of = protocol
    with cls(SCRIPT) as double:
        _, _, data = _request(double.base_url, "POST", path, body())
    tokens_in, tokens_out = usage_of(data)
    assert isinstance(tokens_in, int) and tokens_in >= 0
    assert isinstance(tokens_out, int) and tokens_out >= 0


def test_double_answers_404_for_an_unknown_path(protocol):
    cls, _, body, _, _ = protocol
    with cls(SCRIPT) as double:
        status, _, _ = _request(double.base_url, "POST", "/v1/not-a-route", body())
    assert status == 404


def test_double_binds_loopback_on_an_ephemeral_port(protocol):
    cls, _, _, _, _ = protocol
    with cls(SCRIPT) as double:
        parts = urlsplit(double.base_url)
    assert parts.hostname == "127.0.0.1"
    assert parts.port not in (None, 0)
    assert parts.path in ("", "/")


def test_double_refuses_connections_after_the_context_exits(protocol):
    cls, path, body, _, _ = protocol
    with cls(SCRIPT) as double:
        base_url = double.base_url
    with pytest.raises(OSError):
        _request(base_url, "POST", path, body())


# --- protocol-specific wire shape -----------------------------------------------------


def test_responses_stream_is_created_then_text_deltas_then_completed():
    with ResponsesDouble(SCRIPT) as double:
        _, _, data = _request(double.base_url, "POST", "/v1/responses", _responses_body())
    names = [name for name, _ in _sse_events(data)]
    assert names[0] == "response.created"
    assert names[-1] == "response.completed"
    middle = names[1:-1]
    assert middle and all(n == "response.output_text.delta" for n in middle)
    # The SSE event name and the JSON "type" field agree, as the Responses API does.
    for name, payload in _sse_events(data):
        assert json.loads(payload)["type"] == name


def test_messages_stream_has_the_anthropic_event_order():
    with MessagesDouble(SCRIPT) as double:
        _, _, data = _request(double.base_url, "POST", "/v1/messages", _messages_body())
    names = [name for name, _ in _sse_events(data)]
    assert names[:2] == ["message_start", "content_block_start"]
    assert names[-3:] == ["content_block_stop", "message_delta", "message_stop"]
    deltas = names[2:-3]
    assert deltas and all(n == "content_block_delta" for n in deltas)
    for name, payload in _sse_events(data):
        assert json.loads(payload)["type"] == name


def test_gemini_stream_is_bare_data_lines_with_candidates_and_usage_metadata():
    with GenerateContentDouble(SCRIPT) as double:
        _, _, data = _request(double.base_url, "POST",
                              "/v1beta/models/m1:streamGenerateContent?alt=sse",
                              _gemini_body())
    assert all(name is None for name, _ in _sse_events(data))
    chunks = _json_payloads(data)
    assert chunks and all("candidates" in c for c in chunks)
    assert "usageMetadata" in chunks[-1]


# --- chat completions: cross-check against the kilo double -------------------------------


def _serve_kilo_double(script):
    cfg = sm._Config(argparse.Namespace(script=script, tool_name="write", tool_args="",
                                        chunks=0, interval=0.0, log=""))
    server = ThreadingHTTPServer(("127.0.0.1", 0), sm._make_handler(cfg))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_chat_double_stream_matches_kilo_scripted_model_for_the_same_script():
    body = {"model": "m1", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
    kilo_server, kilo_url = _serve_kilo_double("text")
    try:
        _, _, kilo_bytes = _request(kilo_url, "POST", "/v1/chat/completions", body)
    finally:
        kilo_server.shutdown()
        kilo_server.server_close()
    with ChatCompletionsDouble(["done"]) as double:
        _, _, double_bytes = _request(double.base_url, "POST", "/v1/chat/completions", body)
    assert double_bytes == kilo_bytes
    assert double_bytes.endswith(b"data: [DONE]\n\n")


def test_chat_double_has_no_usage_chunk_unless_asked_for_one():
    body = {"model": "m1", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
    with ChatCompletionsDouble(["done"]) as double:
        _, _, data = _request(double.base_url, "POST", "/v1/chat/completions", body)
    assert all("usage" not in chunk for chunk in _json_payloads(data))


def test_chat_double_emits_usage_only_when_include_usage_is_set():
    with ChatCompletionsDouble(["done"]) as double:
        _, _, data = _request(double.base_url, "POST", "/v1/chat/completions", _chat_body())
    final = _json_payloads(data)[-1]
    assert final["choices"] == []
    assert final["usage"]["total_tokens"] == (
        final["usage"]["prompt_tokens"] + final["usage"]["completion_tokens"])
