# ReceiptBox — 付款前，让 AI 先审一遍票 · MCP Server + Web App

[![CI](https://github.com/Zhiyilang/receiptbox/actions/workflows/ci.yml/badge.svg)](https://github.com/Zhiyilang/receiptbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero-dependency core](https://img.shields.io/badge/core-stdlib_only-brightgreen)]()

**ReceiptBox is a functioning MCP server that audits invoices before you pay them.**
Four tools — `extract_document`, `audit_document`, `risk_score`, `payment_link` —
exposed over **three transports** (stdio + Streamable HTTP spec 2025-11-25 +
Gradio web app), plus a **simulated Alexa+ experience** that proves the loop.

> Hackathons: *Amazon Developer Hackathon* (Alexa+ track + AWS Builder &
> Open Source mini challenges) · *Hugging Face Gradio November Hackathon* —
> Track 1 (MCP Servers).

---

## Why

Every finance team pays invoices. Some of those invoices are wrong: the
subtotal and tax don't add up to the total, the tax rate doesn't match the
amount, the same invoice number appears twice (double-payment risk), the date
is in the future. These are cheap to catch and expensive to miss.

ReceiptBox catches them **with evidence** — every finding names the exact
fields and numbers it was derived from. Nothing is a black box, and nothing
is ever fabricated: a field that can't be extracted stays empty.

## The four MCP tools

| Tool | Input | Output |
|---|---|---|
| `extract_document` | messy invoice/receipt text | structured fields (vendor, no, dates, currency, subtotal, tax, total, status) |
| `audit_document` | KEY:VALUE blocks (`---` separated) or messy text | 7 risk-rule findings + score + band, each with `evidence` |
| `risk_score` | single invoice text | score 0–100, band, summary, top findings |
| `payment_link` | invoice text or explicit fields | PayPal collection link (live Orders with credentials, labelled demo link otherwise) |

Risk rules: `TOTAL_MISMATCH` (subtotal + tax ≠ total), `TAX_MISMATCH`
(declared rate vs implied), `DUPLICATE` (same vendor+no / vendor+amount+date —
the double-payment killer), `MISSING_FIELD`, `FUTURE_DATE`, `STALE_UNPAID`,
`ROUND_AMOUNT`.

## Three transports, one brain

```
                     ┌──────────────────────────────┐
   MCP host ──stdio──▶  receiptbox.mcp_server        │
 (Claude Desktop,     │  zero-dependency JSON-RPC   │
  Cline, any agent)   │  2.0 over stdio             │
                     └──────────────┬───────────────┘
                                    │ same tool layer
                     ┌──────────────▼───────────────┐
   MCP host ──HTTP──▶  receiptbox.http_mcp (:7861)   │
 (Alexa+, any host)   │  Streamable HTTP, spec      │
                     │  2025-11-25 + sessions       │
                     └──────────────┬───────────────┘
                                    │ same tool layer
                     ┌──────────────▼──────────────┐
   Human ──browser──▶  Gradio app (app.py)          │
                     │  4 tool tabs + JSON-RPC tab │
                     └──────────────────────────────┘
                     ┌──────────────────────────────┐
   "Alexa, …" ──────▶  alexa_sim.py (:7862)         │
                     │  simulated Alexa+ experience │
                     │  live MCP calls, spoken     │
                     │  reply + receipt card       │
                     └──────────────────────────────┘
```

The protocol layers (`mcp_server.py` stdio, `http_mcp.py` Streamable HTTP)
and the web layers (`app.py`, `alexa_sim.py`) all call the same pure
functions in `receiptbox/tools.py` — what an agent gets over the wire is
exactly what a human gets in the browser.

### Alexa+ track: self-hosted MCP server (spec 2025-11-25)

```bash
python -m receiptbox.http_mcp --port 7861     # Streamable HTTP + Mcp-Session-Id
PORT=7862 python alexa_sim.py                 # simulated Alexa+ experience
```

The HTTP server implements the Streamable HTTP transport with zero
dependencies: single POST endpoint, SSE-framed responses
(`event: message`), server-assigned `Mcp-Session-Id`, `initialize`
version negotiation (client-requested version if supported, else latest),
`202 Accepted` for notifications, `DELETE` to terminate a session,
`405` for GET. `alexa_sim.py` then runs the full agent loop against it —
PERCEIVE → PLAN → MCP `tools/call` → EXPLAIN → RECEIPT — with every call
visible on screen, and an automatic in-process fallback if the server is
unreachable (labelled `offline`, the demo never dies).

### Three extraction brains (priority: Bedrock → LLM → offline)

| Brain | Needs | Notes |
|---|---|---|
| `aws_brain.BedrockExtractor` | `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` | Bedrock Converse API, **hand-written SigV4** (stdlib hmac/sha256) |
| `extract.LLMExtractor` | `RECEIPTBOX_LLM_BASE`/`RECEIPTBOX_LLM_KEY` | any OpenAI-compatible endpoint |
| `extract.OfflineExtractor` | nothing | deterministic regex engine, always available |

## Quick start

### 1. As an MCP server (stdio) — e.g. Claude Desktop

```json
{
  "mcpServers": {
    "receiptbox": {
      "command": "python",
      "args": ["-m", "receiptbox.mcp_server"],
      "cwd": "/path/to/receiptbox"
    }
  }
}
```

Then ask: *"Audit this invoice: Subtotal 8800, Tax 500, Total 9300"* →
`TOTAL_MISMATCH` with evidence `subtotal=8800.0 tax=500.0 total=9300.0`.

### 2. As an MCP server (Streamable HTTP) + web app

```bash
pip install -r requirements.txt
python app.py          # http://localhost:7860  ·  MCP at /gradio_api/mcp/
```

Connect any MCP client to `http://localhost:7860/gradio_api/mcp/`.
The **JSON-RPC tab** in the UI lets you speak the protocol by hand — no
client needed:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
```

### 3. As a library

```python
from receiptbox import audit_document
print(audit_document(open("invoice.txt").read()))
```

## Design principles

1. **Evidence or it didn't happen.** Every finding carries the fields and
   numbers behind it. The score formula is `min(100, HIGH*30 + CAUTION*10)` —
   published, reproducible, arguable.
2. **Never fabricate.** The extractor returns empty for fields it can't find.
   A missing total produces a `MISSING_FIELD` caution, not an invented number.
3. **Dual brain, graceful degradation.** Offline regex brain (zero-dependency,
   deterministic, always available) + optional OpenAI-compatible LLM brain for
   messy OCR text. Set `RECEIPTBOX_LLM_BASE/KEY/MODEL` to upgrade; unset it and
   nothing breaks.
4. **Zero-dependency core.** `pip install` nothing: the protocol, extractor,
   auditor and receipt renderer are pure stdlib. Gradio is only the web shell.
5. **Payments are real when credentials are real.** `payment_link` creates a
   genuine PayPal Orders v2 order with sandbox credentials; without them it
   returns a clearly-labelled demo deep-link — never a fake "live" link.

## Testing

```bash
python eval.py
```

56 assertions, all offline, covering: extractor field/guards/edges, the 7
risk rules + scoring, in-process JSON-RPC (initialize / tools/list /
tools/call / notifications / error paths), **a real subprocess stdio
session** (`initialize → notifications/initialized → tools/list → tools/call ×2
→ stdin EOF → clean exit 0`), **a real Streamable HTTP server + client
session** (spec 2025-11-25: session id, version negotiation, SSE framing,
`202` notifications, unknown-tool error path), **SigV4 signing** (determinism
+ header construction + brain-selection chain), and **the Alexa+ simulator
loop** (e2e over real MCP + offline fallback). The same tool layer is
additionally exercised through the official `mcp` Python SDK against the
Gradio HTTP endpoint during development.

## Repository layout

```
receiptbox/
  models.py       Invoice / Finding / Report dataclasses (to_dict for wire)
  extract.py      dual-brain extractor: OfflineExtractor + LLMExtractor
  aws_brain.py    Bedrock Converse extractor with hand-written SigV4 (stdlib)
  audit.py        7 risk rules + scoring (transparent, evidence-first)
  paylink.py      PayPal Orders v2 (live) / demo deep-link (fallback)
  tools.py        the 4 tool functions — shared by protocol and web layers
  mcp_server.py   zero-dependency JSON-RPC 2.0 MCP server (stdio)
  http_mcp.py     zero-dependency MCP server, Streamable HTTP, spec 2025-11-25
  mcp_client.py   zero-dependency Streamable HTTP client (stdlib)
app.py            Gradio app: 4 tool tabs + JSON-RPC passthrough, mcp_server=True
alexa_sim.py      simulated Alexa+ experience: live MCP calls + spoken reply
eval.py           56 offline assertions
examples/         sample invoices (messy + structured) + SDK client demo
AMAZON_SUBMISSION.md   Devpost text for the Amazon hackathon
FEEDBACK.md           product feedback (Alexa+/MCP, Bedrock, GitHub)
```

## Configuration

| Variable | Effect |
|---|---|
| `RECEIPTBOX_LLM_BASE` / `RECEIPTBOX_LLM_KEY` / `RECEIPTBOX_LLM_MODEL` | enable the LLM extraction brain |
| `RECEIPTBOX_PAYPAL_CLIENT_ID` / `RECEIPTBOX_PAYPAL_SECRET` | enable live PayPal order creation |

## License

MIT — see [LICENSE](LICENSE).
