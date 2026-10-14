"""ReceiptBox 离线断言套件（评委加分项）：python eval.py

覆盖七层：
  A. 提取引擎（离线双大脑 + AWS Bedrock SigV4 大脑选择链）
  B. 风控引擎——7 类风险规则 + 评分 + 证据
  C. MCP 协议（进程内分发）
  D. MCP stdio 传输——真实子进程往返
  E. MCP Streamable HTTP 传输——spec 2025-11-25，真服务 + 真客户端
     （Amazon Alexa+ 赛道硬要求）
  F. AWS Bedrock 大脑——stdlib SigV4 签名 + 大脑优先级
  G. 模拟 Alexa+ 体验——agent 闭环 + MCP 不可达时的离线降级
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date

from receiptbox.extract import OfflineExtractor, get_extractor, parse_structured
from receiptbox.audit import audit
from receiptbox.mcp_server import handle_message, TOOLS, PROTOCOL_VERSION

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        FAILURES.append(name)
        print("  [FAIL] %s %s" % (name, detail))


MESSY = """Acme Office Supplies Co., Ltd.
Invoice No: INV-2026-0901
Invoice Date: 2026-09-15
Subtotal: 13400.00
Tax Rate: 13%
VAT: 1742.00
Total: 15142.00
Status: unpaid
"""

STRUCTURED = """vendor: Acme Office Supplies Co., Ltd.
invoice_no: INV-2026-0901
date: 2026-09-15
subtotal: 13400.00
tax_rate: 13%
tax: 1742.00
total: 15142.00
status: unpaid
---
vendor: Acme Office Supplies Co., Ltd.
invoice_no: INV-2026-0901
date: 2026-09-15
subtotal: 13400.00
tax: 1742.00
total: 15142.00
status: unpaid
"""

print("== A. extract engine ==")
ex = OfflineExtractor()
inv = ex.extract(MESSY)
check("extract.vendor", inv.vendor == "Acme Office Supplies Co., Ltd.", inv.vendor)
check("extract.invoice_no", inv.invoice_no == "INV-2026-0901", inv.invoice_no)
check("extract.date", inv.date_str == "2026-09-15", inv.date_str)
check("extract.subtotal", inv.subtotal == 13400.00, str(inv.subtotal))
check("extract.total", inv.total == 15142.00, str(inv.total))
check("extract.tax", inv.tax == 1742.00, str(inv.tax))
check("extract.tax_rate", abs(inv.tax_rate - 0.13) < 1e-9, str(inv.tax_rate))
check("extract.status", inv.status == "unpaid", inv.status)

# 守卫：Subtotal 行不得污染 total；Tax Rate 行不得污染 tax
inv2 = ex.extract("Subtotal: 500.00\nTax Rate: 13%\nTotal: 565.00")
check("guard.subtotal-not-total", inv2.total == 565.00, str(inv2.total))
check("guard.taxrate-not-tax", inv2.tax is None, str(inv2.tax))

# 守卫：unpaid 含子串 paid，必须先判未付
inv3 = ex.extract("Total: 99.00\nStatus: UNPAID")
check("guard.unpaid-before-paid", inv3.status == "unpaid", inv3.status)

# 结构化解析
invs = parse_structured(STRUCTURED)
check("structured.count", len(invs) == 2, str(len(invs)))
check("structured.fields", invs[0].total == 15142.00 and invs[0].vendor.startswith("Acme"),
      str(invs[0].total))

print("== B. audit engine ==")
rep = audit([inv])
check("audit.clean", rep.score == 0 and rep.band == "LOW", rep.summary)

# 价税不平 → HIGH
bad = ex.extract("Vendor: X Co., Ltd.\nInvoice No: A-1\nDate: 2026-01-01\n"
                 "Subtotal: 100.00\nTax: 6.00\nTotal: 200.00")
rep_bad = audit([bad])
codes = [f.code for f in rep_bad.findings]
check("audit.total-mismatch", "TOTAL_MISMATCH" in codes, str(codes))
check("audit.high-band", rep_bad.band == "HIGH", rep_bad.band)
f = [f for f in rep_bad.findings if f.code == "TOTAL_MISMATCH"][0]
check("audit.evidence", "subtotal=100.0" in f.evidence, f.evidence)

# 重复票 → HIGH
rep_dup = audit(parse_structured(STRUCTURED))
codes_dup = [f.code for f in rep_dup.findings]
check("audit.duplicate", "DUPLICATE" in codes_dup, str(codes_dup))

# 未来日期
fut = ex.extract("Vendor: Y\nInvoice No: B-2\nDate: 2099-01-01\nTotal: 10.00")
rep_fut = audit([fut], today=date(2026, 10, 15))
check("audit.future-date", "FUTURE_DATE" in [f.code for f in rep_fut.findings])

print("== C. MCP protocol (in-process) ==")
r = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"}})
check("mcp.initialize", r["result"]["serverInfo"]["name"] == "receiptbox"
      and "tools" in r["result"]["capabilities"], json.dumps(r)[:120])

r = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
names = [t["name"] for t in r["result"]["tools"]]
check("mcp.tools-list", names == ["extract_document", "audit_document",
                                  "risk_score", "payment_link"], str(names))
# payment_link 语义上是 text/total 二选一，不强制 required；其余三个必须必填
strict = [t for t in r["result"]["tools"] if t["name"] != "payment_link"]
check("mcp.schema-required", all("required" in t["inputSchema"] for t in strict))

r = handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "extract_document",
                               "arguments": {"text": MESSY}}})
payload = json.loads(r["result"]["content"][0]["text"])
check("mcp.tools-call", payload["invoice"]["total"] == 15142.00
      and r["result"]["isError"] is False, r["result"]["content"][0]["text"][:80])

# 未知工具 → 协议级错误 -32602（MCP 规范：工具不存在=invalid params，
# 而非 isError 结果；isError 保留给"工具跑了但业务失败"）
r = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "nope", "arguments": {}}})
check("mcp.unknown-tool", r.get("error", {}).get("code") == -32602, json.dumps(r)[:120])

# 错误路径：缺参数 → isError=true 且原因可读
r = handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "risk_score", "arguments": {}}})
payload = json.loads(r["result"]["content"][0]["text"])
check("mcp.bad-args", r["result"]["isError"] is True and "text" in payload["error"],
      payload.get("error", ""))

# 通知无响应
r = handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
check("mcp.notification-silent", r is None)

print("== D. MCP protocol (real subprocess over stdio) ==")


def _stdio_session(messages, timeout=40):
    """真实 MCP 客户端会话：写入全部消息 → 关闭 stdin → 读到 EOF。

    注意：通知（notifications/*）按协议不产生响应，客户端绝不应在发出
    通知后做阻塞读——这是客户端侧的误用。这里用 communicate() 由
    subprocess 正确排空所有管道，子进程在 stdin EOF 后干净退出。
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "receiptbox.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=".",
    )
    payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    out, err = proc.communicate(payload, timeout=timeout)
    responses = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return responses, proc.returncode, err


responses, rc, stderr_txt = _stdio_session([
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "audit_document", "arguments": {"text": STRUCTURED}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "payment_link",
                "arguments": {"text": "Total: 99.50\nStatus: unpaid"}}},
])

by_id = {r["id"]: r for r in responses if isinstance(r, dict) and "id" in r}
check("stdio.clean-exit", rc == 0, "rc=%s stderr=%s" % (rc, stderr_txt[:200]))
check("stdio.response-count", len(by_id) == 4, str(sorted(by_id)))
r1 = by_id.get(1, {})
check("stdio.initialize", r1.get("result", {}).get("protocolVersion") == PROTOCOL_VERSION,
      json.dumps(r1)[:120])
r2 = by_id.get(2, {})
check("stdio.tools-list", len(r2.get("result", {}).get("tools", [])) == 4)
r3 = by_id.get(3, {})
payload = json.loads(r3["result"]["content"][0]["text"])
check("stdio.audit", payload["report"]["score"] >= 30
      and any(f["code"] == "DUPLICATE" for f in payload["report"]["findings"]),
      json.dumps(payload)[:120])
r4 = by_id.get(4, {})
payload = json.loads(r4["result"]["content"][0]["text"])
check("stdio.payment_link", payload["payment_link"].startswith("https://")
      and payload["amount"] == 99.50, payload.get("payment_link", ""))

print("== E. MCP over Streamable HTTP (spec 2025-11-25, real server) ==")
# Amazon Alexa+ 赛道硬要求：self-hosted MCP server，spec 2025-11-25+，
# Streamable HTTP 传输。这里起真服务 + 真客户端做端到端验证。
import threading

from receiptbox.http_mcp import make_server
from receiptbox.mcp_client import MCPError, StreamableHttpClient

http_srv = make_server("127.0.0.1", 0, "/mcp")
http_port = http_srv.server_address[1]
t = threading.Thread(target=http_srv.serve_forever, daemon=True)
t.start()

client = StreamableHttpClient("http://127.0.0.1:%d/mcp" % http_port, timeout=20)
info = client.initialize("2025-11-25")
check("http.initialize", info.get("protocolVersion") == "2025-11-25"
      and info.get("serverInfo", {}).get("name") == "receiptbox",
      json.dumps(info)[:120])
check("http.session-id", bool(client.session_id), str(client.session_id))

tools = client.list_tools()
check("http.tools-list", [x["name"] for x in tools] ==
      ["extract_document", "audit_document", "risk_score", "payment_link"],
      str([x["name"] for x in tools]))

res = client.call_tool("audit_document", {"text": STRUCTURED})
check("http.tools-call", any(f["code"] == "DUPLICATE"
                             for f in res["report"]["findings"]),
      json.dumps(res)[:120])

# 版本协商：客户端要一个不存在的版本 → 服务端回最新受支持版本
c2 = StreamableHttpClient("http://127.0.0.1:%d/mcp" % http_port, timeout=20)
info2 = c2.initialize("1999-01-01")
check("http.version-negotiation", info2.get("protocolVersion") == "2025-11-25",
      str(info2.get("protocolVersion")))

# 错误路径：未知工具 → JSON-RPC error（客户端抛 MCPError）
try:
    c2.call_tool("nope", {})
    check("http.unknown-tool", False, "no error raised")
except MCPError as e:
    check("http.unknown-tool", "unknown tool" in str(e), str(e)[:100])

# 通知类消息 → 202 Accepted，无响应体（客户端拿到 None，不抛错）
resp = client._post({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {}})
check("http.notification-202", resp is None, str(resp))

http_srv.shutdown()

print("== F. AWS Bedrock brain (stdlib SigV4, zero-dependency) ==")
from receiptbox.aws_brain import (BedrockExtractor, signature_key,
                                  signed_headers)

k1 = signature_key("secret", "20261015", "us-east-1", "bedrock-runtime")
k2 = signature_key("secret", "20261015", "us-east-1", "bedrock-runtime")
check("aws.sigv4-deterministic", k1 == k2 and len(k1) == 32)
hdrs = signed_headers("POST",
                      "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
                      "anthropic.claude-3-haiku-20240307-v1:0/converse",
                      "us-east-1", "AKID", "SECRET", b'{"a":1}')
auth = hdrs.get("Authorization", "")
check("aws.sigv4-header", auth.startswith("AWS4-HMAC-SHA256 Credential=AKID/")
      and "SignedHeaders=" in auth and "x-amz-date" in auth, auth[:100])
import re as _re
check("aws.amz-date-format", bool(_re.match(r"^\d{8}T\d{6}Z$", hdrs["x-amz-date"])),
      hdrs.get("x-amz-date", ""))

# 大脑选择链：有 AWS 凭据 → Bedrock；只有 OpenAI 兼容 → LLM；都没有 → 离线
os.environ["AWS_ACCESS_KEY_ID"] = "AKID-TEST"
os.environ["AWS_SECRET_ACCESS_KEY"] = "SECRET-TEST"
check("aws.brain-selection", isinstance(get_extractor(), BedrockExtractor),
      type(get_extractor()).__name__)
del os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"]
check("aws.brain-offline", type(get_extractor()).__name__ == "OfflineExtractor",
      type(get_extractor()).__name__)

print("== G. Simulated Alexa+ experience (agent loop) ==")
import alexa_sim

cases = [("Alexa，这张票有没有风险", "risk"),
         ("Alexa，检查有没有重复票据", "duplicate"),
         ("Alexa，帮我审计这些票", "audit"),
         ("Alexa，生成催收链接", "payment"),
         ("Alexa，提取字段", "extract"),
         ("你好", "help")]
for utt, want in cases:
    p = alexa_sim.plan(utt)
    check("sim.plan.%s" % want, p["intent"] == want, str(p))

# 端到端：指向刚起过的 HTTP MCP 服务（重启一个），mode 应为 mcp
http_srv2 = make_server("127.0.0.1", 0, "/mcp")
t2 = threading.Thread(target=http_srv2.serve_forever, daemon=True)
t2.start()
alexa_sim.MCP_URL = "http://127.0.0.1:%d/mcp" % http_srv2.server_address[1]
turn = alexa_sim.run_turn("Alexa，这张票有没有风险", MESSY)
check("sim.e2e-mode", turn["mode"] == "mcp", turn["mode"])
check("sim.e2e-steps", [s["phase"] for s in turn["steps"]] ==
      ["PERCEIVE", "PLAN", "MCP", "MCP", "EXPLAIN", "RECEIPT"],
      str([s["phase"] for s in turn["steps"]]))
check("sim.e2e-reply", "风险" in turn["reply"], turn["reply"][:80])
check("sim.e2e-card", turn["card"].get("band") in ("LOW", "MEDIUM", "HIGH"),
      json.dumps(turn["card"])[:100])
http_srv2.shutdown()

# 降级路径：MCP 不可达 → offline 模式仍然给出回复（演示永不崩）
alexa_sim.MCP_URL = "http://127.0.0.1:1/mcp"
turn2 = alexa_sim.run_turn("Alexa，帮我审计这些票", STRUCTURED)
check("sim.fallback-mode", turn2["mode"] == "offline", turn2["mode"])
check("sim.fallback-reply", bool(turn2["reply"]), turn2["reply"][:80])

print()
print("=" * 56)
print("RESULT: %d passed, %d failed" % (PASS, FAIL))
if FAILURES:
    print("FAILED:", ", ".join(FAILURES))
print("=" * 56)
sys.exit(1 if FAIL else 0)
