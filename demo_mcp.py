#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_mcp.py - drive the ReceiptBox MCP server over stdio, live.

Spawns `python -m receiptbox.mcp_server` as a real child process and performs
a genuine JSON-RPC session: initialize -> tools/list -> audit_document ->
payment_link. Every request and response printed here really crossed the
pipe; nothing is mocked.

Run:  python demo_mcp.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# A duplicate pair plus an arithmetic mismatch - the two risks that matter
# most before paying an invoice.
DUPLICATE_PAIR = """Invoice: A-1042
Vendor: Northwind Traders
Date: 2026-09-02
Currency: USD
Subtotal: 13400.00
Tax: 1742.00
Total: 15142.00
Tax rate: 13%
Status: unpaid
---
Invoice: A-1042
Vendor: Northwind Traders
Date: 2026-09-02
Currency: USD
Subtotal: 13400.00
Tax: 1742.00
Total: 15142.00
Tax rate: 13%
Status: unpaid
---
Invoice: B-2210
Vendor: Capex Solutions
Date: 2026-09-05
Currency: USD
Subtotal: 980.00
Tax: 127.40
Total: 1207.40
Tax rate: 13%
Status: unpaid"""

SINGLE = """Total: 99.50
Currency: USD
Status: unpaid"""


def _env():
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _wait_port(port: int, timeout: float = 12.0) -> bool:
    """Poll until the HTTP MCP server accepts connections."""
    import socket
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def show(label, obj, keys=None):
    print(">>> %s" % label)
    if keys and isinstance(obj, dict):
        slim = {k: obj[k] for k in keys if k in obj}
        print(json.dumps(slim, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2)[:1600])
    print()


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "receiptbox.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", cwd=HERE, env=_env(),
    )
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-11-25",
                    "capabilities": {}, "clientInfo": {"name": "demo", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "audit_document", "arguments": {"text": DUPLICATE_PAIR}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "payment_link", "arguments": {"text": SINGLE}}},
    ]
    payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs)
    out, err = proc.communicate(payload, timeout=60)
    if err.strip():
        print("[server stderr]", err.strip()[:400])

    responses = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and "id" in msg:
            responses[msg["id"]] = msg

    print("=" * 72)
    print("ReceiptBox MCP stdio session - real child process, real pipe")
    print("=" * 72)
    print()
    r1 = responses.get(1, {}).get("result", {})
    show("initialize -> protocol + session",
         {"protocolVersion": r1.get("protocolVersion"),
          "serverInfo": r1.get("serverInfo")})
    r2 = responses.get(2, {}).get("result", {})
    tools = [t.get("name") for t in r2.get("tools", [])]
    show("tools/list -> %d tools" % len(tools), tools)
    r3 = responses.get(3, {}).get("result", {})
    try:
        audit = json.loads(r3["content"][0]["text"])
    except Exception:
        audit = {}
    findings = audit.get("report", audit).get("findings", [])
    print(">>> tools/call audit_document -> findings")
    for f in findings:
        print("  [%s] %s" % (f.get("severity", "?"), f.get("code", "?")))
        ev = f.get("evidence", "")
        if ev:
            print("      evidence: %s" % str(ev)[:110])
    print("  score: %s" % audit.get("report", audit).get("score"))
    print()
    r4 = responses.get(4, {}).get("result", {})
    try:
        link = json.loads(r4["content"][0]["text"])
    except Exception:
        link = {}
    show("tools/call payment_link ->",
         {"amount": link.get("amount"), "payment_link": link.get("payment_link")})
    print("server exited rc=%d - %d responses over stdio"
          % (proc.returncode, len(responses)))
    if proc.returncode != 0 or len(responses) != 4:
        return 1

    # ---- Act 2: the same tool layer over Streamable HTTP -----------------
    print()
    print("=" * 72)
    print("Act 2 - same tools, Streamable HTTP transport")
    print("=" * 72)
    print()
    srv = subprocess.Popen(
        [sys.executable, "-m", "receiptbox.http_mcp", "--port", "7861"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", cwd=HERE, env=_env(),
    )
    try:
        if not _wait_port(7861):
            print("[http server did not come up in time]")
            return 1
        banner = srv.stdout.readline().strip()
        print("$ python -m receiptbox.http_mcp --port 7861")
        if banner:
            print("  %s" % banner)
        print()
        sys.path.insert(0, HERE)
        from receiptbox.mcp_client import StreamableHttpClient  # noqa: E402
        client = StreamableHttpClient("http://127.0.0.1:7861/mcp", timeout=20)
        init = client.initialize()
        show("HTTP initialize -> protocol + session",
             {"protocolVersion": init.get("protocolVersion"),
              "serverInfo": init.get("serverInfo")})
        tools = [t.get("name") for t in client.list_tools()]
        show("HTTP tools/list -> %d tools" % len(tools), tools)
        audit = client.call_tool("audit_document", {"text": DUPLICATE_PAIR})
        findings = audit.get("report", audit).get("findings", [])
        print(">>> HTTP tools/call audit_document -> findings")
        for f in findings:
            print("  [%s] %s" % (f.get("severity", "?"), f.get("code", "?")))
        print("  score: %s" % audit.get("report", audit).get("score"))
        print()
        print("one tool layer, three transports - stdio, HTTP, web")
        client.close()
    except Exception as exc:  # noqa: BLE001
        print("[http transport unavailable: %s]" % exc)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:  # noqa: BLE001
            srv.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
