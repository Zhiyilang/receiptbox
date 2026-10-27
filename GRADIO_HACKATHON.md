# Gradio November Hackathon — Submission Guide (ReceiptBox)

**Track 1: Building MCP** — ReceiptBox is already a *functioning MCP server*
with a Gradio UI. This repo is submission-ready; you only deploy + post.

## What's ready (AI-built)

- `app.py` — Gradio app **with `mcp_server=True`**: exposes the 4 tools at
  `/gradio_api/mcp/` AND a 5-tab web UI (extract / audit / risk / payment /
  raw JSON-RPC).
- `receiptbox/` package — zero-dependency MCP server (stdio + Streamable HTTP)
  with 4 tools: `extract_document`, `audit_document`, `risk_score`, `payment_link`.
- `requirements.txt` — `gradio[mcp]>=5.0`.
- `eval.py` — **68/68 assertions pass** (deterministic, offline).

## 4 MCP tools exposed

| Tool | Purpose |
|---|---|
| `extract_document` | messy invoice text → structured fields (never fabricates) |
| `audit_document` | arithmetic mismatch / wrong tax / duplicate / missing fields, each with evidence |
| `risk_score` | one-shot 0-100 risk band + summary |
| `payment_link` | unpaid invoice → PayPal collection/dunning link |

> ⚠️ **官方 hackathon tag 必填（铁律）**：本仓 `README.md` 已含 HF Space 元数据前缀
> （title/emoji/sdk/tags 等），但 `tags` 里的 `hackathon` 是**占位**。窗口开启后，
> 去官方 rules 页复制**确切的 hackathon tag**（形如 `gradio-november-hackathon-2026`，
> 以官方为准），替换 `README.md` 的 `tags` 中 `hackathon` 那一项。
> **tag 错/漏 = 评委检索不到 = 无效提交**，务必核对。

## Your steps (window 2026-11-14 → 11-30, must be NEW during window)

1. **Create a NEW HF Space** (https://huggingface.co/new-space) named e.g.
   `receiptbox-mcp`, pick **Gradio** SDK — *during* the window.
2. Upload `app.py`, `receiptbox/` package, `requirements.txt`, `README.md`,
   `LICENSE` (git clone + push is easiest).
3. Confirm `/gradio_api/mcp/` answers `tools/list`.
4. **Post** a short X/LinkedIn note with the Space link (required).
5. Record a 1–5 min demo (paste a bad invoice → audit flags the mismatch with
   evidence). Upload to YouTube.
6. Submit before 11-30.

## Space README snippet

```
---
title: ReceiptBox — Invoice Audit MCP Server
emoji: 🧾
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: "5.0"
app_file: app.py
pinned: false
tags:
  - mcp
  - gradio-mcp
  - finance
  - agents
---

# ReceiptBox — Building MCP (Track 1)

A functioning MCP server that audits invoices before you pay them...
```

## Eligibility note

Re-verify on the official rules page that mainland China is not excluded at
window open (sister Devpost events have been China-safe).
