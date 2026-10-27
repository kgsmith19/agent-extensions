"""Deterministic OpenAI-compatible model server for the Kilo canary.

This is the ONE double in the canary. It stands in for the LLM provider and
nothing else: the Kilo parser, permission engine, tool dispatch and launcher
under test are the real pinned CLI. Run it as a separate process:

    python3 -I canary/kilo/scripted_model.py --script tool-once \\
        --tool-name write --tool-args '{"filePath":"allowed.txt","content":"x"}' \\
        --ready-file READY --log requests.jsonl

Scripts:
  tool-once    No tool result in the request yet -> emit exactly one tool call
               (--tool-name / --tool-args). Once a tool result is present, answer
               "done". A resumed session already carries the tool result, so it
               answers "done" straight away.
  stream-slow  Every request streams --chunks text deltas, --interval seconds
               apart, and never calls a tool. Used to cancel a run mid-stream.
  text         Answer "done" immediately.

Every request is appended to --log as one JSON line (roles seen, tool names
offered, pid), and every streamed chunk as a "chunk" line, so the runner can
prove what the model received and when a stream is live.

Stdlib only. Binds 127.0.0.1; --port 0 picks a free port, written to --ready-file.
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPTS = ("tool-once", "stream-slow", "text")


class _Config:
    def __init__(self, args):
        self.script = args.script
        self.tool = json.loads(args.tool_args) if args.tool_args else None
        self.tool_name = args.tool_name
        self.chunks = args.chunks
        self.interval = args.interval
        self.log_path = args.log
        self.lock = threading.Lock()

    def log(self, record):
        if not self.log_path:
            return
        record.setdefault("pid", os.getpid())
        record.setdefault("time", time.time())
        with self.lock, open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


def _make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # keep stdout/stderr clean
            pass

        def _send_json(self, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # model listing, if the CLI probes it
            self._send_json({"object": "list", "data": [{"id": "m1", "object": "model"}]})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length) or b"{}")
            messages = req.get("messages", [])
            roles = [m.get("role") for m in messages]
            tools = [t.get("function", {}).get("name") for t in req.get("tools", []) or []]
            cfg.log({"event": "request", "path": self.path, "roles": roles,
                     "tools_offered": tools, "stream": bool(req.get("stream")),
                     "message_count": len(messages)})
            has_tool_result = "tool" in roles
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self._stream(req, has_tool_result)
            except (BrokenPipeError, ConnectionResetError):
                # The client aborted (cancellation). Record it and stop quietly.
                cfg.log({"event": "client_disconnected"})
            self.close_connection = True

        def _event(self, payload):
            self.wfile.write(b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n")
            self.wfile.flush()

        def _chunk(self, delta, finish=None):
            return {"id": "chatcmpl-canary", "object": "chat.completion.chunk", "created": 0,
                    "model": "m1",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        def _stream(self, req, has_tool_result):
            if cfg.script == "stream-slow":
                self._event(self._chunk({"role": "assistant", "content": ""}))
                for i in range(cfg.chunks):
                    self._event(self._chunk({"content": f"tok{i} "}))
                    cfg.log({"event": "chunk", "index": i})
                    time.sleep(cfg.interval)
                self._event(self._chunk({}, "stop"))
            elif cfg.script == "tool-once" and not has_tool_result and cfg.tool:
                call = {"index": 0, "id": "call_1", "type": "function",
                        "function": {"name": cfg.tool_name,
                                     "arguments": json.dumps(cfg.tool)}}
                self._event(self._chunk({"role": "assistant", "tool_calls": [call]}))
                self._event(self._chunk({}, "tool_calls"))
                cfg.log({"event": "tool_call_emitted", "name": cfg.tool_name})
            else:
                self._event(self._chunk({"role": "assistant", "content": "done"}))
                self._event(self._chunk({}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--script", choices=SCRIPTS, default="text")
    parser.add_argument("--tool-name", default="write")
    parser.add_argument("--tool-args", default="")
    parser.add_argument("--chunks", type=int, default=200)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log", default="")
    parser.add_argument("--ready-file", default="")
    args = parser.parse_args(argv)
    if args.script == "tool-once" and not args.tool_args:
        parser.error("--script tool-once needs --tool-args")
    cfg = _Config(args)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _make_handler(cfg))
    if args.ready_file:
        tmp = args.ready_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(str(server.server_address[1]))
        os.replace(tmp, args.ready_file)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
