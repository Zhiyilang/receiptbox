"""ReceiptBox MCP 客户端 —— 纯标准库 Streamable HTTP 客户端。

被 alexa_sim.py（模拟 Alexa+ 体验）和测试套件复用：
  initialize → 保留下发的 Mcp-Session-Id → notifications/initialized
  → tools/list → tools/call（SSE 帧解析）
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class MCPError(RuntimeError):
    pass


def _parse_sse(body: str):
    """把 SSE 帧体解析成 JSON（取 event: message 的 data 行）。"""
    data_lines = []
    for line in body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    return json.loads("\n".join(data_lines))


class StreamableHttpClient:
    """最小可用的 MCP Streamable HTTP 客户端（零依赖）。"""

    def __init__(self, url: str, timeout: int = 30) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id = None

    def _post(self, msg, expect_body=True):
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8")
                if resp.status == 202 or not raw.strip():
                    return None
                return _parse_sse(raw)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise MCPError("HTTP %s: %s" % (e.code, detail[:200]))

    def initialize(self, protocol_version="2025-11-25") -> dict:
        resp = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": protocol_version,
                       "capabilities": {},
                       "clientInfo": {"name": "receiptbox-stdlib-client",
                                      "version": "1.1.0"}},
        })
        if not resp or "result" not in resp:
            raise MCPError("initialize failed: %r" % (resp,))
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp["result"]

    def list_tools(self) -> list:
        resp = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return (resp or {}).get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        resp = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}})
        if resp is None:
            raise MCPError("no response for tools/call %s" % name)
        if "error" in resp:
            raise MCPError("JSON-RPC error %s: %s"
                           % (resp["error"].get("code"),
                              resp["error"].get("message")))
        result = resp.get("result", {})
        if result.get("isError"):
            text = result["content"][0]["text"]
            raise MCPError("tool %s failed: %s" % (name, text[:200]))
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        body = b""
        req = urllib.request.Request(self.url, data=body, method="DELETE",
                                     headers={"Mcp-Session-Id":
                                              self.session_id or ""})
        try:
            urllib.request.urlopen(req, timeout=self.timeout).read()
        except Exception:
            pass
