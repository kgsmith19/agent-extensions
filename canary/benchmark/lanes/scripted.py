"""Scripted wire-protocol doubles for the clean-room benchmark (#91).

One loopback HTTP server base, four thin protocol renderers:

  ChatCompletionsDouble   OpenAI chat completions (kilo). POST /v1/chat/completions.
  ResponsesDouble         OpenAI Responses API (codex). POST /v1/responses.
  MessagesDouble          Anthropic Messages API (claude-code). POST /v1/messages.
  GenerateContentDouble   Gemini (gemini-cli). POST /v1beta/models/{m}:streamGenerateContent?alt=sse.

Each double is a context manager bound to 127.0.0.1 on an ephemeral port. It
exposes `.base_url` (scheme, host and port only) and `.requests` (the parsed
JSON bodies of every request on a known path, in arrival order). It serves one
scripted reply per request, in order. An unknown path gets 404; a request
after the script is exhausted gets 500, so a run never silently reuses a reply.

Stdlib only, in process, loopback only: no network.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_MODEL = "m1"


def _data(payload):
    """A bare `data:` SSE frame (chat completions and Gemini)."""
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


def _named(name, payload):
    """An `event:` + `data:` SSE frame (Responses and Messages)."""
    return f"event: {name}\n".encode("utf-8") + _data(payload)


def _tokens_in(request):
    """Deterministic stand-in for input tokens: whitespace tokens of the request body."""
    return len(json.dumps(request).split())


def _tokens_out(reply):
    """Deterministic stand-in for output tokens: whitespace tokens of the reply."""
    return len(reply.split())


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # keep stdout and stderr clean
        pass

    def do_POST(self):
        double = self.server.double
        if not double.route.fullmatch(urlsplit(self.path).path):
            self._send(404, "application/json", b'{"error":"unknown path"}')
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        try:
            body = double._serve(request)
        except LookupError as exc:
            self._send(500, "application/json",
                       json.dumps({"error": str(exc)}).encode("utf-8"))
            return
        self._send(200, "text/event-stream", body)

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class _Double:
    """Loopback server base: records requests and serves one scripted reply each."""

    route = None  # compiled regex, matched against the path with the query stripped

    def __init__(self, script):
        self._script = list(script)
        self._next = 0
        self._lock = threading.Lock()
        self.requests = []
        self.base_url = None
        self._server = None
        self._thread = None

    def encode(self, request, reply):
        """Render one reply as the protocol's SSE body bytes."""
        raise NotImplementedError

    def _serve(self, request):
        with self._lock:
            self.requests.append(request)
            if self._next >= len(self._script):
                raise LookupError("scripted replies exhausted")
            reply = self._script[self._next]
            self._next += 1
        return self.encode(request, reply)

    def __enter__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.double = self
        host, port = self._server.server_address[:2]  # advertise the address actually bound
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()  # serve_forever polls at 0.5s by default; keep teardown fast
        self._server.server_close()
        self._thread.join()
        return False


class ChatCompletionsDouble(_Double):
    """OpenAI chat completions stream. Usage only when stream_options.include_usage is set."""

    route = re.compile(r"/v1/chat/completions")

    def encode(self, request, reply):
        def chunk(delta, finish=None):
            return {"id": "chatcmpl-canary", "object": "chat.completion.chunk", "created": 0,
                    "model": _MODEL,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        frames = [_data(chunk({"role": "assistant", "content": reply})),
                  _data(chunk({}, "stop"))]
        if (request.get("stream_options") or {}).get("include_usage"):
            prompt, completion = _tokens_in(request), _tokens_out(reply)
            frames.append(_data({
                "id": "chatcmpl-canary", "object": "chat.completion.chunk", "created": 0,
                "model": _MODEL, "choices": [],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                          "total_tokens": prompt + completion}}))
        frames.append(b"data: [DONE]\n\n")
        return b"".join(frames)


class ResponsesDouble(_Double):
    """OpenAI Responses API stream, in the sequence codex 0.165.0 requires.

    codex drops text deltas that arrive without an active output item, so the
    message item is opened and closed around the deltas, and response.completed
    carries the finished item in its output. Verified against the real binary.
    """

    route = re.compile(r"/v1/responses")

    def encode(self, request, reply):
        item_id = "msg_canary"
        text_part = {"type": "output_text", "text": "", "annotations": []}
        open_item = {"id": item_id, "type": "message", "role": "assistant",
                     "status": "in_progress", "content": []}
        done_part = {"type": "output_text", "text": reply, "annotations": []}
        done_item = dict(open_item, status="completed", content=[done_part])
        response = {"id": "resp_canary", "object": "response", "model": _MODEL,
                    "status": "in_progress", "output": []}
        prompt, completion = _tokens_in(request), _tokens_out(reply)
        completed = dict(response, status="completed", output=[done_item], usage={
            "input_tokens": prompt, "output_tokens": completion,
            "total_tokens": prompt + completion})
        events = [
            ("response.created", {"type": "response.created", "response": response}),
            ("response.output_item.added", {
                "type": "response.output_item.added", "output_index": 0, "item": open_item}),
            ("response.content_part.added", {
                "type": "response.content_part.added", "item_id": item_id,
                "output_index": 0, "content_index": 0, "part": text_part}),
            ("response.output_text.delta", {
                "type": "response.output_text.delta", "item_id": item_id,
                "output_index": 0, "content_index": 0, "delta": reply}),
            ("response.output_text.done", {
                "type": "response.output_text.done", "item_id": item_id,
                "output_index": 0, "content_index": 0, "text": reply}),
            ("response.content_part.done", {
                "type": "response.content_part.done", "item_id": item_id,
                "output_index": 0, "content_index": 0, "part": done_part}),
            ("response.output_item.done", {
                "type": "response.output_item.done", "output_index": 0, "item": done_item}),
            ("response.completed", {"type": "response.completed", "response": completed}),
        ]
        return b"".join(_named(name, payload) for name, payload in events)


class MessagesDouble(_Double):
    """Anthropic Messages stream: message_start, one text block, message_delta, message_stop."""

    route = re.compile(r"/v1/messages")

    def encode(self, request, reply):
        prompt, completion = _tokens_in(request), _tokens_out(reply)
        frames = [
            _named("message_start", {"type": "message_start", "message": {
                "id": "msg_canary", "type": "message", "role": "assistant", "model": _MODEL,
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": prompt, "output_tokens": 0}}}),
            _named("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}),
            _named("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": reply}}),
            _named("content_block_stop", {"type": "content_block_stop", "index": 0}),
            _named("message_delta", {"type": "message_delta",
                                     "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                     "usage": {"output_tokens": completion}}),
            _named("message_stop", {"type": "message_stop"}),
        ]
        return b"".join(frames)


class GenerateContentDouble(_Double):
    """Gemini streamGenerateContent with alt=sse: bare data frames, usage on the last."""

    route = re.compile(r"/v1beta/models/[^/:]+:streamGenerateContent")

    def encode(self, request, reply):
        prompt, completion = _tokens_in(request), _tokens_out(reply)
        return _data({
            "candidates": [{"content": {"role": "model", "parts": [{"text": reply}]},
                            "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": prompt,
                              "candidatesTokenCount": completion,
                              "totalTokenCount": prompt + completion},
            "modelVersion": _MODEL,
        })
