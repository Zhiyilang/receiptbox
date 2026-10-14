"""ReceiptBox —— 付款前 AI 审票 MCP 服务器（零依赖纯标准库）。

一条工具层，三种传输：
  stdio             → receiptbox/mcp_server.py    `python -m receiptbox.mcp_server`
  Streamable HTTP   → receiptbox/http_mcp.py      `python -m receiptbox.http_mcp`
                      （MCP 规范 2025-11-25，Alexa+ 赛道硬要求）
  Web / MCP(Gradio) → app.py（Hugging Face Space）

两个参赛用途：
  · Amazon Developer Hackathon（Alexa+ 赛道）——self-hosted MCP server
    + alexa_sim.py 模拟 Alexa+ 体验
  · Hugging Face Gradio November Hackathon（Track 1 MCP Servers）

四个 MCP 工具：
  extract_document / audit_document / risk_score / payment_link
"""

from .models import Invoice, Finding, Report
from .extract import (OfflineExtractor, LLMExtractor,
                      get_extractor, parse_structured)
from .aws_brain import BedrockExtractor, get_bedrock_extractor
from .audit import audit
from .paylink import PayLink
from .tools import extract_document, audit_document, risk_score, payment_link
from .mcp_server import (TOOLS, handle_message, serve, PROTOCOL_VERSION,
                         SUPPORTED_VERSIONS)
from .http_mcp import make_server as make_http_server
from .mcp_client import StreamableHttpClient

__version__ = "1.1.0"
