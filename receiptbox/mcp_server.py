"""ReceiptBox MCP Server —— 零依赖纯标准库实现（JSON-RPC 2.0 over stdio）。

这是 Hugging Face Hackathon Track 1 的交付物：一个 functioning MCP server。
不依赖 gradio / mcp SDK，任何 MCP 宿主（Claude Desktop、Cline、自建 agent）
都可以直接以 `python -m receiptbox.mcp_server` 启动它。

协议（Model Context Protocol）：
  initialize            → protocolVersion / capabilities / serverInfo
  notifications/initialized（通知，无响应）
  ping                  → {}
  tools/list            → 工具清单（name / description / inputSchema）
  tools/call            → {content:[{type:text,text}], isError:bool}

暴露的 4 个工具：
  extract_document  杂乱票据文本 → 结构化字段（离线/LLM 双大脑）
  audit_document    结构化 KEY:VALUE 多票 → 风险发现 + 评分
  risk_score        单张票据文本 → 一键风险分档
  payment_link      未付票 → PayPal 收款/催收链接

所有工具结果都带 evidence 字段（可解释性），失败时 isError=true 且给出
可读原因——绝不静默编造。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from .tools import extract_document, audit_document, risk_score, payment_link

PROTOCOL_VERSION = "2025-11-25"  # Alexa+ 赛道要求的 MCP 规范版本
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
SERVER_INFO = {"name": "receiptbox", "version": "1.1.0"}

# JSON-RPC 错误码（规范定义）
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

TOOLS = [
    {
        "name": "extract_document",
        "description": (
            "Extract structured fields from a messy invoice/receipt text "
            "(vendor, invoice_no, date, currency, subtotal, tax, total, status). "
            "Dual-brain: offline regex extractor by default, LLM when an "
            "OpenAI-compatible key is configured. Never fabricates fields."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "Raw invoice/receipt text (any layout)."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "audit_document",
        "description": (
            "Audit one or more invoices for payment risks: arithmetic mismatch "
            "(subtotal+tax!=total), wrong tax rate, duplicate invoices "
            "(double-payment risk), missing fields, future dates, stale unpaid, "
            "round amounts. Input is KEY:VALUE blocks separated by '---', or "
            "messy text (auto-extracted first). Every finding carries evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "KEY:VALUE blocks separated by ---, or messy text."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "risk_score",
        "description": (
            "One-shot risk triage for a single invoice text: returns score "
            "(0-100), band (LOW/MEDIUM/HIGH) and a human-readable summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw invoice text."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "payment_link",
        "description": (
            "Generate a PayPal collection/dunning link for an unpaid invoice. "
            "With PayPal sandbox credentials configured it creates a real "
            "Orders v2 order; otherwise it returns a deterministic demo deep-link. "
            "Fields may be omitted — they are extracted from raw text when given."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw invoice text (optional)."},
                "total": {"type": "number", "description": "Amount due."},
                "currency": {"type": "string", "description": "ISO code, e.g. USD."},
                "invoice_no": {"type": "string"},
                "vendor": {"type": "string"},
            },
        },
    },
]


def _err(code, message, req_id=None):
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _ok(result, req_id):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _text_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


# ---------------------------------------------------------------------------
# 工具实现（纯函数层，与 Gradio 应用共用；输入非法抛 ValueError）
# ---------------------------------------------------------------------------
from .tools import extract_document, audit_document, risk_score, payment_link


def _tool_extract_document(args: dict) -> dict:
    return extract_document(args.get("text", ""))


def _tool_audit_document(args: dict) -> dict:
    return audit_document(args.get("text", ""))


def _tool_risk_score(args: dict) -> dict:
    return risk_score(args.get("text", ""))


def _tool_payment_link(args: dict) -> dict:
    return payment_link(
        text=args.get("text", ""), total=args.get("total"),
        currency=args.get("currency", ""), invoice_no=args.get("invoice_no", ""),
        vendor=args.get("vendor", ""),
    )


_TOOL_IMPL = {
    "extract_document": _tool_extract_document,
    "audit_document": _tool_audit_document,
    "risk_score": _tool_risk_score,
    "payment_link": _tool_payment_link,
}


# ---------------------------------------------------------------------------
# JSON-RPC 分发
# ---------------------------------------------------------------------------
def handle_message(msg: dict) -> Optional[dict]:
    """处理一条 JSON-RPC 消息；通知类返回 None。"""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(INVALID_REQUEST, "expected a JSON-RPC 2.0 object",
                    msg.get("id") if isinstance(msg, dict) else None)
    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg
    params = msg.get("params") or {}

    # ---- 通知（无响应） ----
    if method == "notifications/initialized":
        return None
    if method and method.startswith("notifications/"):
        return None

    # ---- initialize（版本协商：客户端要的版本在支持列表就回声，否则给最新）----
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        return _ok({
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "ReceiptBox audits invoices before you pay them: extract fields, "
                "detect arithmetic mismatches / duplicates / tax errors with "
                "evidence, and generate PayPal collection links for unpaid bills."
            ),
        }, req_id)

    if method == "ping":
        return _ok({}, req_id)

    if method == "tools/list":
        return _ok({"tools": TOOLS}, req_id)

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        impl = _TOOL_IMPL.get(name)
        if impl is None:
            if is_notification:
                return None
            return _err(INVALID_PARAMS, f"unknown tool: {name!r}", req_id)
        try:
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            return _ok(_text_result(impl(arguments)), req_id)
        except ValueError as e:
            # 参数/输入问题 → 可读错误，isError=true（不是协议错误）
            return _ok(_text_result({"error": str(e)}, is_error=True), req_id)
        except Exception as e:  # 兜底：绝不把堆栈暴露给宿主
            return _ok(_text_result({"error": f"internal: {type(e).__name__}: {e}"},
                                    is_error=True), req_id)

    if is_notification:
        return None
    return _err(METHOD_NOT_FOUND, f"method not found: {method!r}", req_id)


def _force_utf8(stream) -> None:
    """把真实 std 流锁定为 UTF-8。

    Windows 的管道默认用本地代码页（如 cp936）：写含非 GBK 字符的响应会
    UnicodeEncodeError 直接打死子进程，读侧同理。MCP over stdio 与平台
    locale 无关，这里显式锁定 UTF-8，父子进程都用 UTF-8 即可完全确定。
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass


def serve(stdin=None, stdout=None) -> None:
    """stdio 传输：一行一条 JSON（MCP 标准传输方式）。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if stdin is sys.stdin:
        _force_utf8(stdin)
    if stdout is sys.stdout:
        _force_utf8(stdout)
    trace = os.environ.get("RECEIPTBOX_TRACE")
    if trace:
        with open(trace, "a", encoding="utf-8") as tf:
            tf.write("serve-start pid=%d\n" % os.getpid())
    try:
        for line in stdin:
            line = line.strip()
            if trace:
                with open(trace, "a", encoding="utf-8") as tf:
                    tf.write("line: %r\n" % line[:80])
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                resp = _err(PARSE_ERROR, f"parse error: {e}")
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()
                continue
            resp = handle_message(msg)
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()
                if trace:
                    with open(trace, "a", encoding="utf-8") as tf:
                        tf.write("responded id=%s\n" % resp.get("id"))
    except BrokenPipeError:
        # 宿主提前关闭 stdout：安静退岀，不把堆栈抛给管道
        pass
    if trace:
        with open(trace, "a", encoding="utf-8") as tf:
            tf.write("loop-exit (stdin EOF)\n")


if __name__ == "__main__":
    serve()
