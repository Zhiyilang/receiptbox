"""ReceiptBox 工具层：4 个 MCP 工具的纯函数实现（dict in → dict out）。

MCP server（mcp_server.py）与 Gradio 应用（app.py）共用这一层，
保证「协议里调到的」和「界面上点的」是同一份逻辑。

约定：输入非法 / 业务失败一律抛 ValueError（消息可读），
由上层转换成 MCP 的 isError 结果或 UI 错误——绝不静默编造。
"""

from __future__ import annotations

from .extract import get_extractor, parse_structured
from .audit import audit
from .paylink import payment_link as _payment_link


def extract_document(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required and must be non-empty")
    inv = get_extractor().extract(text)
    return {"invoice": inv.to_dict(),
            "brain": inv.source,
            "evidence": "fields extracted from raw text; empty means not found (never fabricated)"}


def audit_document(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required and must be non-empty")
    invoices = parse_structured(text) if ":" in text else []
    if not invoices:
        invoices = [get_extractor().extract(text)]
    rep = audit(invoices, brain=invoices[0].source)
    return {"report": rep.to_dict()}


def risk_score(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required and must be non-empty")
    inv = get_extractor().extract(text)
    rep = audit([inv], brain=inv.source)
    return {"score": rep.score, "band": rep.band, "summary": rep.summary,
            "invoice": inv.to_dict(),
            "top_findings": [
                {"severity": f.severity, "code": f.code, "label": f.label,
                 "detail": f.detail, "evidence": f.evidence}
                for f in rep.findings[:5]
            ]}


def payment_link(text: str = "", total=None, currency: str = "",
                 invoice_no: str = "", vendor: str = "") -> dict:
    text = (text or "").strip()
    # 显式字段缺失时从 raw text 补抽（铁律：不编造）
    if text and (total is None or not invoice_no):
        inv = get_extractor().extract(text)
        total = total if total is not None else inv.total
        currency = currency or inv.currency
        invoice_no = invoice_no or inv.invoice_no
        vendor = vendor or inv.vendor
    if total is None:
        raise ValueError("no amount found: pass 'total' or text containing a total")
    link = _payment_link(total=float(total), currency=str(currency or "USD"),
                         invoice_no=str(invoice_no or ""), vendor=str(vendor or ""))
    return {"payment_link": link.url, "mode": link.mode,
            "amount": float(total), "currency": str(currency or "USD"),
            "evidence": link.evidence}
