"""模拟 Alexa+ 体验 —— ReceiptBox 的语音式控制台（纯标准库）。

Amazon Developer Hackathon · Alexa+ 赛道两条路径都覆盖：
  1) self-hosted MCP server（spec 2025-11-25+, Streamable HTTP）
     → receiptbox/http_mcp.py
  2) "Build a simulated Alexa+ experience in a web app using your
     preferred agentic tool" → 就是本应用：本文件

体验闭环（与真机 Alexa+ 同构）：
  用户 utterance → PERCEIVE（意图识别）→ PLAN（选工具/填参数）
  → MCP tools/call（走 Streamable HTTP，实时可见）→ EXPLAIN（语音式短回复）
  → CARD（结构化回执）

MCP 服务不可达时自动降级为进程内直调（标注 offline），演示永不崩。
启动：python alexa_sim.py   （PORT 环境变量，默认 7862）
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen

from receiptbox.mcp_client import StreamableHttpClient
from receiptbox.tools import (audit_document, extract_document,
                              payment_link, risk_score)

MCP_URL = os.environ.get("RECEIPTBOX_MCP_URL", "http://127.0.0.1:7861/mcp")

# ---------------------------------------------------------------------------
# 意图路由（确定性规划器：utterance → 工具 + 参数）
# ---------------------------------------------------------------------------
_INTENTS = [
    (r"重复|duplicate|double.?pay|是不是付过", "duplicate",
     "audit_document", {"focus": "duplicates"}),
    (r"风险|risk|score|能付|能不能付|安全吗", "risk",
     "risk_score", {}),
    (r"链接|link|催收|dunning|collection|催一下|收款", "payment",
     "payment_link", {}),
    (r"审计|审|audit|检查|核对|不平|税", "audit",
     "audit_document", {}),
    (r"提取|抽取|extract|字段|结构化|识别", "extract",
     "extract_document", {}),
]


def plan(utterance: str) -> dict:
    """PERCEIVE + PLAN：返回意图、工具名与参数。"""
    low = utterance.lower()
    for pattern, intent, tool, extra in _INTENTS:
        if re.search(pattern, low):
            return {"intent": intent, "tool": tool, "extra": extra}
    return {"intent": "help", "tool": None, "extra": {}}


def _speak(tool_result: dict, intent: str) -> str:
    """EXPLAIN：把工具结果压缩成 Alexa 式的一句话。"""
    if intent == "duplicate":
        dups = [f for f in tool_result.get("report", {}).get("findings", [])
                if f.get("code") == "DUPLICATE"]
        if dups:
            return ("小心，我发现 %d 组重复票据，可能是重复付款。"
                    "建议先人工确认再付。" % len(dups))
        return "没有发现重复票据，可以放心付这一张。"
    if intent == "risk":
        band = tool_result.get("band", "?")
        score = tool_result.get("score", "?")
        reply = "这张票风险分 %s，档位 %s。" % (score, band)
        if band == "HIGH":
            reply += "建议暂停付款，先看卡片里的问题清单。"
        elif band == "MEDIUM":
            reply += "有几处需要留意，详情在卡片里。"
        else:
            reply += "看起来干净。"
        return reply
    if intent == "payment":
        return ("收款链接已生成：%s。金额 %s %s，已放到卡片里，复制即可发客户。"
                % (tool_result.get("payment_link", ""),
                   tool_result.get("amount"), tool_result.get("currency")))
    if intent == "audit":
        rep = tool_result.get("report", {})
        n = len(rep.get("findings", []))
        if n == 0:
            return "审计完成，未发现明显风险，可以付款。"
        return ("审计完成，发现 %d 处风险，总分 %s。最严重的：%s。"
                % (n, rep.get("score"),
                   rep["findings"][0].get("detail", "")[:80]))
    if intent == "extract":
        inv = tool_result.get("invoice", {})
        return ("提取完成：%s，票号 %s，价税合计 %s %s，状态 %s。"
                % (inv.get("vendor") or "未知商家", inv.get("invoice_no") or "无",
                   inv.get("total"), inv.get("currency"),
                   inv.get("status") or "未知"))
    return ("我是 ReceiptBox，帮你在付款前审票。可以问我："
            "「这张票有没有风险」「检查有没有重复」「生成催收链接」。")


_LOCAL_TOOLS = {
    "audit_document": audit_document,
    "risk_score": risk_score,
    "payment_link": payment_link,
    "extract_document": extract_document,
}


def _local_call(tool: str, args: dict) -> dict:
    """进程内直调（MCP 服务不可达时的降级路径，参数契约一致）。"""
    fn = _LOCAL_TOOLS[tool]
    if tool == "payment_link":
        return fn(text=args.get("text", ""))
    return fn(text=args.get("text", ""))


def run_turn(utterance: str, invoice_text: str) -> dict:
    """完整 agent 回合：PERCEIVE→PLAN→ACT(MCP)→EXPLAIN→RECEIPT。"""
    steps = []
    p = plan(utterance)
    steps.append({"phase": "PERCEIVE", "detail": "utterance: %r" % utterance})
    steps.append({"phase": "PLAN",
                  "detail": "intent=%s → tool=%s" % (p["intent"], p["tool"])})

    if not p["tool"]:
        steps.append({"phase": "EXPLAIN", "detail": "help intent, no tool"})
        return {"steps": steps, "reply": _speak({}, "help"),
                "card": None, "mode": "local"}

    args = {"text": invoice_text} if invoice_text else {}
    if p["tool"] == "payment_link" and not invoice_text:
        args = {}

    mode = "mcp"
    try:
        client = StreamableHttpClient(MCP_URL, timeout=20)
        info = client.initialize()
        steps.append({"phase": "MCP",
                      "detail": "initialize ok, protocol=%s, session=%s…"
                                % (info.get("protocolVersion"),
                                   (client.session_id or "")[:8])})
        tool_result = client.call_tool(p["tool"], args)
        client.close()
        steps.append({"phase": "MCP",
                      "detail": "tools/call %s → ok" % p["tool"]})
    except Exception as e:  # 降级：进程内直调，演示永不崩
        mode = "offline"
        steps.append({"phase": "MCP",
                      "detail": "MCP unreachable (%s); in-process fallback"
                                % str(e)[:80]})
        try:
            tool_result = _local_call(p["tool"], args)
        except Exception as e2:
            tool_result = {"error": str(e2)}

    reply = _speak(tool_result, p["intent"])
    steps.append({"phase": "EXPLAIN", "detail": reply})
    steps.append({"phase": "RECEIPT", "detail": "card delivered to user"})
    return {"steps": steps, "reply": reply, "card": tool_result, "mode": mode}


# ---------------------------------------------------------------------------
# Web 控制台（纯标准库）
# ---------------------------------------------------------------------------
CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', system-wide, sans-serif; background: #f4f6fb;
       color: #1c2333; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; } .sub { color: #5b6478; font-size: 13px; margin: 6px 0 18px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.card { background: #fff; border: 1px solid #e3e8f2; border-radius: 14px;
        padding: 18px; box-shadow: 0 1px 3px rgba(20,30,60,.05); }
.device { background: linear-gradient(160deg, #232a3d, #3a4570); color: #fff; }
.orb { width: 84px; height: 84px; border-radius: 50%; margin: 12px auto;
       background: radial-gradient(circle at 35% 35%, #6fd6ff, #2b7fe0 60%, #174a8f);
       box-shadow: 0 0 0 0 rgba(64,150,255,.55); animation: pulse 2.4s infinite; }
.orb.thinking { animation: pulse .7s infinite; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(64,150,255,.5); }
       70% { box-shadow: 0 0 0 22px rgba(64,150,255,0); }
       100% { box-shadow: 0 0 0 0 rgba(64,150,255,0); } }
.status { text-align: center; font-size: 13px; color: #aeb9d6; margin-bottom: 10px; }
.reply { background: rgba(255,255,255,.1); border-radius: 12px; padding: 14px;
         font-size: 15px; line-height: 1.55; min-height: 66px; }
.cardpre { background: #0f1420; color: #b7f5c9; border-radius: 10px; padding: 12px;
           font: 12px/1.5 Consolas, monospace; white-space: pre-wrap;
           max-height: 220px; overflow: auto; margin-top: 10px; }
textarea { width: 100%; border: 1px solid #d5dcec; border-radius: 10px;
           padding: 10px; font: 13px/1.5 Consolas, monospace; min-height: 130px;
           resize: vertical; }
input[type=text] { width: 100%; border: 1px solid #d5dcec; border-radius: 10px;
           padding: 11px 13px; font-size: 14px; margin: 10px 0; }
button { background: #2b7fe0; color: #fff; border: 0; border-radius: 10px;
         padding: 11px 20px; font-size: 14px; cursor: pointer; }
button:hover { background: #1f6cc4; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.chip { background: #e8eefb; color: #23447c; border-radius: 999px;
        padding: 7px 13px; font-size: 12.5px; cursor: pointer; border: 0; }
.chip:hover { background: #d5e3fa; }
.steps { margin-top: 12px; } .step { display: flex; gap: 10px; padding: 7px 0;
        border-bottom: 1px dashed #e3e8f2; font-size: 13px; }
.ph { flex: 0 0 92px; font-weight: 700; color: #2b7fe0; font-size: 11.5px;
      letter-spacing: .4px; padding-top: 2px; }
.dt { color: #3d4763; word-break: break-all; }
.mode { font-size: 11px; color: #8a93ab; margin-top: 8px; }
label { font-size: 12.5px; color: #5b6478; display: block; margin: 12px 0 6px; }
"""

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>ReceiptBox · 模拟 Alexa+ 体验</title>
<style>__CSS__</style></head>
<body><div class="wrap">
<h1>ReceiptBox — 付款前，先问一句 Alexa</h1>
<div class="sub">模拟 Alexa+ 体验 · 后端是 self-hosted MCP server（spec 2025-11-25, Streamable HTTP）· 每个动作实时可见</div>
<div class="grid">
  <div class="card device">
    <div class="orb" id="orb"></div>
    <div class="status" id="status">待命 — 说「Alexa，帮我审这张票」</div>
    <div class="reply" id="reply">你好，我是 ReceiptBox。粘贴一张发票，然后告诉我你想做什么。</div>
    <div class="cardpre" id="card" style="display:none"></div>
  </div>
  <div class="card">
    <label>发票 / 收据文本（粘贴）</label>
    <textarea id="invoice">__SAMPLE__</textarea>
    <div class="chips">
      <button class="chip" data-u="Alexa，这张票有没有风险？">有没有风险</button>
      <button class="chip" data-u="Alexa，检查有没有重复票据">查重复</button>
      <button class="chip" data-u="Alexa，帮我审计这些票">审计</button>
      <button class="chip" data-u="Alexa，生成催收链接">催收链接</button>
      <button class="chip" data-u="Alexa，提取字段">提取字段</button>
    </div>
    <input type="text" id="utterance" placeholder="Alexa，这张票能不能付？">
    <button id="ask">问 ReceiptBox</button>
    <div class="steps" id="steps"></div>
    <div class="mode" id="mode"></div>
  </div>
</div></div>
<script>
var SAMPLE = document.getElementById('invoice').value;
function esc(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function chip(u){ document.getElementById('utterance').value = u; ask(); }
document.querySelectorAll('.chip').forEach(function(b){
  b.onclick = function(){ chip(b.getAttribute('data-u')); };
});
function renderSteps(steps){
  var box = document.getElementById('steps'); box.innerHTML = '';
  steps.forEach(function(s, i){
    var row = document.createElement('div'); row.className = 'step';
    row.innerHTML = '<div class="ph">' + esc(s.phase) + '</div><div class="dt">' + esc(s.detail) + '</div>';
    box.appendChild(row);
  });
}
function ask(){
  var u = document.getElementById('utterance').value.trim() || 'Alexa，帮我审这张票';
  var inv = document.getElementById('invoice').value;
  var orb = document.getElementById('orb'), st = document.getElementById('status');
  orb.className = 'orb thinking'; st.textContent = '思考中…';
  fetch('/ask', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({utterance:u, invoice_text:inv})})
  .then(function(r){ return r.json(); })
  .then(function(d){
    orb.className = 'orb'; st.textContent = '待命';
    document.getElementById('reply').textContent = d.reply;
    var card = document.getElementById('card');
    if (d.card){ card.style.display='block';
      card.textContent = JSON.stringify(d.card, null, 1).slice(0, 1800); }
    else { card.style.display='none'; }
    renderSteps(d.steps);
    document.getElementById('mode').textContent =
      'transport: ' + (d.mode === 'mcp' ? 'MCP over Streamable HTTP' : 'in-process fallback (MCP server unreachable)');
  })
  .catch(function(e){ orb.className='orb'; st.textContent='出错了：' + e; });
}
document.getElementById('ask').onclick = ask;
document.getElementById('utterance').addEventListener('keydown', function(e){
  if (e.key === 'Enter') ask();
});
</script></body></html>"""

SAMPLE_INVOICE = ("Acme Office Supplies Co., Ltd.\n"
                  "Invoice No: INV-2026-0901\n"
                  "Invoice Date: 2026-09-15\n"
                  "Subtotal: 13400.00\n"
                  "Tax Rate: 13%\n"
                  "VAT: 1742.00\n"
                  "Total: 15142.00\n"
                  "Status: unpaid")


class SimHandler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/healthz":
            self._json(200, {"ok": True, "mcp_url": MCP_URL})
            return
        page = PAGE.replace("__CSS__", CSS).replace("__SAMPLE__", SAMPLE_INVOICE)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/ask":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._json(400, {"error": "invalid JSON"})
            return
        result = run_turn(str(data.get("utterance", "")),
                          str(data.get("invoice_text", "")))
        self._json(200, result)

    def log_message(self, fmt, *args):
        if os.environ.get("RECEIPTBOX_TRACE"):
            sys.stderr.write("[sim] %s\n" % (fmt % args))


def main() -> None:
    port = int(os.environ.get("PORT", "7862"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), SimHandler)
    print("ReceiptBox Alexa+ simulator on http://0.0.0.0:%d (MCP: %s)"
          % (port, MCP_URL))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
