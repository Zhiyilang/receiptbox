"""ReceiptBox MCP Server —— Streamable HTTP 传输（MCP 规范 2025-11-25）。

Amazon Developer Hackathon · Alexa+ 赛道硬要求：
  "Build a self-hosted MCP server (spec 2025-11-25 or later, Streamable HTTP)"

本模块用纯标准库实现该传输：
  POST {prefix}   一条 JSON-RPC 消息（或批量数组）
                  initialize 时下发 Mcp-Session-Id 头，后续请求必须带回
                  会话缺失/无效/已终止 → 400（spec 要求，不是 404）
                  内容协商：Accept 含 text/event-stream → SSE 帧；
                          只含 application/json → 单条 JSON（不喂 SSE 帧）
  通知（无 id）→ 202 Accepted，空响应体
  GET  {prefix}   405（本服务不提供服务端→客户端 SSE 流）
  DELETE {prefix} 销毁会话 → 200

协议分发完全复用 mcp_server.handle_message（与 stdio 传输同一份逻辑）。
启动：python -m receiptbox.http_mcp [--port 7861]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .mcp_server import (PROTOCOL_VERSION, SUPPORTED_VERSIONS, TOOLS,
                         handle_message)

DEFAULT_PATH = "/mcp"


class _SessionStore:
    """进程内会话表（spec: 会话 id 由服务端在 initialize 时分配）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids = set()

    def create(self) -> str:
        sid = uuid.uuid4().hex
        with self._lock:
            self._ids.add(sid)
        return sid

    def valid(self, sid) -> bool:
        if not sid:
            return False
        with self._lock:
            return sid in self._ids

    def drop(self, sid) -> None:
        with self._lock:
            self._ids.discard(sid)


SESSIONS = _SessionStore()


class MCPHttpHandler(BaseHTTPRequestHandler):
    server_version = "ReceiptBoxMCP/1.1"
    protocol_version = "HTTP/1.1"

    # ---- 工具方法 ----
    def _send_json(self, code: int, obj, extra_headers=None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, obj, extra_headers=None) -> None:
        """Streamable HTTP 规定：响应走 text/event-stream，event: message。"""
        data = json.dumps(obj, ensure_ascii=False)
        body = ("event: message\ndata: %s\n\n" % data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _accepted(self, extra_headers=None) -> None:
        """通知类消息：202 Accepted，无响应体。"""
        self.send_response(202)
        self.send_header("Content-Length", "0")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _path_ok(self) -> bool:
        return self.path.split("?", 1)[0] == self.server.mcp_path

    def _wants_sse(self) -> bool:
        """内容协商：客户端 Accept 里没要 text/event-stream 就回单条 JSON。

        spec 2025-11-25 要求客户端声明 `Accept: application/json,
        text/event-stream`；服务端可以任选其一响应。只声明 application/json
        的客户端不该被喂 SSE 帧——它们大多是直接 json.loads(response.body) 的。
        """
        accept = (self.headers.get("Accept") or "").lower()
        if "text/event-stream" in accept:
            return True
        if "application/json" in accept:
            return False
        return True          # 未声明时按 SSE 处理（与既有客户端行为一致）

    def _respond(self, obj, extra_headers=None) -> None:
        if self._wants_sse():
            self._send_sse(obj, extra_headers)
        else:
            self._send_json(200, obj, extra_headers)

    # ---- 传输方法 ----
    def do_POST(self) -> None:
        if not self._path_ok():
            self._send_json(404, {"error": "not found; MCP lives at %s"
                                           % self.server.mcp_path})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700,
                                            "message": "parse error: %s" % e}})
            return

        messages = msg if isinstance(msg, list) else [msg]
        is_initialize = any(isinstance(m, dict) and m.get("method") == "initialize"
                            for m in messages)

        # 会话校验：initialize 分配新会话；其余请求必须带回有效会话
        sid = self.headers.get("Mcp-Session-Id")
        extra = {}
        if is_initialize:
            sid = SESSIONS.create()
            extra["Mcp-Session-Id"] = sid
        elif not SESSIONS.valid(sid):
            # spec 2025-11-25：会话缺失/无效/已终止 → 400 Bad Request
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32000,
                                            "message": "invalid or missing "
                                                       "Mcp-Session-Id"}})
            return

        responses = [handle_message(m) for m in messages]
        responses = [r for r in responses if r is not None]
        if not responses:
            self._accepted(extra)          # 全是通知
        elif len(responses) == 1:
            self._respond(responses[0], extra)
        else:
            self._respond(responses, extra)  # 批量响应

    def do_GET(self) -> None:
        if not self._path_ok():
            self._send_json(404, {"error": "not found"})
            return
        # spec 2025-11-25：服务端可以不提供 GET SSE 流 → 405
        self._send_json(405, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32600,
                                        "message": "GET not supported on this "
                                                   "endpoint; POST JSON-RPC"}})

    def do_DELETE(self) -> None:
        if not self._path_ok():
            self._send_json(404, {"error": "not found"})
            return
        SESSIONS.drop(self.headers.get("Mcp-Session-Id"))
        self._send_json(200, {"ok": True, "terminated": True})

    def log_message(self, fmt, *args):  # 静音默认访问日志
        if os.environ.get("RECEIPTBOX_HTTP_TRACE"):
            sys.stderr.write("[http] %s\n" % (fmt % args))


def make_server(host: str = "127.0.0.1", port: int = 7861,
                path: str = DEFAULT_PATH) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), MCPHttpHandler)
    srv.mcp_path = path
    return srv


def main() -> None:
    ap = argparse.ArgumentParser(description="ReceiptBox MCP over Streamable HTTP")
    ap.add_argument("--host", default=os.environ.get("RECEIPTBOX_MCP_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("RECEIPTBOX_MCP_PORT", "7861")))
    ap.add_argument("--path", default=os.environ.get("RECEIPTBOX_MCP_PATH",
                                                     DEFAULT_PATH))
    args = ap.parse_args()
    srv = make_server(args.host, args.port, args.path)
    print("ReceiptBox MCP (Streamable HTTP, spec %s) on http://%s:%d%s"
          % (PROTOCOL_VERSION, args.host, args.port, args.path))
    print("tools: %s" % ", ".join(t["name"] for t in TOOLS))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
