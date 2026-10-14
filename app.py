"""ReceiptBox · Hugging Face Space 入口（Track 1: MCP Servers）

Gradio 应用 + MCP 服务器二合一：
  - 人类用户：4 个标签页直接用 4 个工具（提取 / 审计 / 风险分 / 收款链接），
    外加「协议透传」页——不装任何 MCP 客户端，直接手写 JSON-RPC 体验协议。
  - MCP 宿主：launch(mcp_server=True) 后在 /gradio_api/mcp/ 暴露全部工具，
    可被 Claude Desktop / Cline / 任意 agent 调用。

工具函数带类型标注与 docstring —— Gradio 据此自动生成 MCP inputSchema。
"""

from __future__ import annotations

import json

import gradio as gr

from receiptbox import tools as rb
from receiptbox.mcp_server import handle_message

DESCRIPTION = """
# ReceiptBox — 付款前，让 AI 先审一遍票

**一个 functioning MCP server**（零依赖纯标准库实现，JSON-RPC 2.0 over stdio），
也是一个人人可用的 Web 应用。4 个工具：

| 工具 | 作用 |
|---|---|
| `extract_document` | 杂乱票据文本 → 结构化字段（离线正则 / LLM 双大脑，不编造） |
| `audit_document` | 价税不平 / 税率错 / 重复票（双付风险）/ 缺字段…每条带证据 |
| `risk_score` | 一键风险分档（0-100 / LOW-MEDIUM-HIGH） |
| `payment_link` | 未付票 → PayPal 收款/催收链接（有凭据走真实 Orders，无凭据演示深链） |

MCP 宿主连接地址：`/gradio_api/mcp/`（Streamable HTTP）。
命令行启动：`python -m receiptbox.mcp_server`（stdio）。
"""


def _fmt(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# MCP 工具函数（函数名 = MCP 工具名；docstring + 类型标注 → inputSchema）
# ---------------------------------------------------------------------------
def extract_document(text: str) -> str:
    """Extract structured fields (vendor, invoice_no, date, currency,
    subtotal, tax, total, status) from a messy invoice/receipt text.
    Dual-brain: offline regex by default, LLM when a key is configured.
    Never fabricates missing fields."""
    return _fmt(rb.extract_document(text))


def audit_document(text: str) -> str:
    """Audit invoices for payment risks: arithmetic mismatch, wrong tax
    rate, duplicate invoices (double-payment risk), missing fields,
    future dates, stale unpaid, round amounts. Input KEY:VALUE blocks
    separated by ---, or messy text. Every finding carries evidence."""
    return _fmt(rb.audit_document(text))


def risk_score(text: str) -> str:
    """One-shot risk triage for a single invoice: score (0-100),
    band (LOW/MEDIUM/HIGH) and a human-readable summary."""
    return _fmt(rb.risk_score(text))


def payment_link(text: str = "", total: str = "", currency: str = "",
                 invoice_no: str = "", vendor: str = "") -> str:
    """Generate a PayPal collection/dunning link for an unpaid invoice.
    Pass raw text (fields auto-extracted) or explicit total/currency/
    invoice_no/vendor. Live PayPal Orders with credentials, else a
    clearly-labelled demo deep-link."""
    args = {"text": text, "currency": currency,
            "invoice_no": invoice_no, "vendor": vendor}
    if total.strip():
        try:
            args["total"] = float(total)
        except ValueError:
            return _fmt({"error": "total must be a number"})
    return _fmt(rb.payment_link(**args))


def load_bad_sample() -> str:
    """UI helper: load a sample invoice whose subtotal+tax != total."""
    return SAMPLE_BAD


def jsonrpc(message: str) -> str:
    """Send a raw JSON-RPC 2.0 message to this MCP server and get the
    response. Try: {"jsonrpc":"2.0","id":1,"method":"tools/list"}"""
    try:
        msg = json.loads(message)
    except json.JSONDecodeError as e:
        return _fmt({"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": f"parse error: {e}"}})
    resp = handle_message(msg)
    return _fmt(resp if resp is not None else {"note": "notification accepted (no response)"})


SAMPLE_MESSY = """Acme Office Supplies Co., Ltd.
Invoice No: INV-2026-0901
Invoice Date: 2026-09-15
Subtotal: 13400.00
Tax Rate: 13%
VAT: 1742.00
Total: 15142.00
Status: unpaid"""

SAMPLE_DUP = """vendor: Acme Office Supplies Co., Ltd.
invoice_no: INV-2026-0901
date: 2026-09-15
subtotal: 13400.00
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
status: unpaid"""

SAMPLE_BAD = """Vendor: Shenzhen XinDa Trading Co., Ltd.
Invoice No: XD-2026-0042
Invoice Date: 2026-01-20
Subtotal: 8800.00
Tax Rate: 13%
Tax: 500.00
Total: 9300.00
Status: unpaid"""

CSS = """
#title {text-align: center; margin-bottom: 0.2em;}
#subtitle {text-align: center; color: #666; margin-top: 0;}
.tool-note {color: #666; font-size: 0.9em;}
"""

with gr.Blocks(title="ReceiptBox — 付款前 AI 审票 MCP Server") as demo:
    gr.Markdown(DESCRIPTION, elem_id="title")

    with gr.Tab("提取字段 extract_document"):
        gr.Markdown("杂乱票据文本 → 结构化字段。空字段 = 没抽到（**绝不编造**）。",
                    elem_classes="tool-note")
        with gr.Row():
            ex_in = gr.Textbox(label="票据文本", lines=12, value=SAMPLE_MESSY)
            ex_out = gr.Code(label="结构化结果（JSON）", language="json")
        gr.Button("提取").click(extract_document, inputs=ex_in, outputs=ex_out)

    with gr.Tab("风险审计 audit_document"):
        gr.Markdown("多张票用 `---` 分隔。7 类风险规则，每条发现带 evidence。",
                    elem_classes="tool-note")
        with gr.Row():
            au_in = gr.Textbox(label="票据（KEY:VALUE 或杂乱文本）", lines=12, value=SAMPLE_DUP)
            au_out = gr.Code(label="审计报告（JSON）", language="json")
        gr.Button("审计").click(audit_document, inputs=au_in, outputs=au_out)
        gr.Button("试试一张价税不平的票").click(
            load_bad_sample, inputs=None, outputs=au_in, api_name=False)

    with gr.Tab("风险分 risk_score"):
        gr.Markdown("单张票据一键分诊：分数 / 等级 / 摘要 / Top 发现。",
                    elem_classes="tool-note")
        with gr.Row():
            rs_in = gr.Textbox(label="票据文本", lines=10, value=SAMPLE_BAD)
            rs_out = gr.Code(label="风险分（JSON）", language="json")
        gr.Button("评分").click(risk_score, inputs=rs_in, outputs=rs_out)

    with gr.Tab("收款链接 payment_link"):
        gr.Markdown("未付票 → PayPal 催收链接。留空则从文本自动抽取。",
                    elem_classes="tool-note")
        with gr.Row():
            with gr.Column():
                pl_text = gr.Textbox(label="票据文本（可选）", lines=6, value=SAMPLE_MESSY)
                pl_total = gr.Textbox(label="金额（可选，留空自动抽）", value="")
                pl_cur = gr.Textbox(label="货币（可选）", value="")
                pl_no = gr.Textbox(label="票号（可选）", value="")
                pl_vendor = gr.Textbox(label="供应商（可选）", value="")
                pl_btn = gr.Button("生成链接")
            pl_out = gr.Code(label="收款链接（JSON）", language="json")
        pl_btn.click(
            payment_link,
            inputs=[pl_text, pl_total, pl_cur, pl_no, pl_vendor],
            outputs=pl_out)

    with gr.Tab("协议透传 JSON-RPC"):
        gr.Markdown(
            "不装 MCP 客户端，直接体验协议。发送 "
            "`{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}` 看工具清单；"
            "`initialize` / `tools/call` 也可。通知（无 id）不产生响应。",
            elem_classes="tool-note")
        with gr.Row():
            rp_in = gr.Textbox(
                label="JSON-RPC 消息", lines=6,
                value='{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}')
            rp_out = gr.Code(label="响应（JSON）", language="json")
        gr.Button("发送").click(jsonrpc, inputs=rp_in, outputs=rp_out)


if __name__ == "__main__":
    import os
    demo.launch(server_name="0.0.0.0",
                server_port=int(os.environ.get("PORT", "7860")),
                css=CSS, mcp_server=True)
