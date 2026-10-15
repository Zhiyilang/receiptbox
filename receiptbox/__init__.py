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

# NOTE: mcp_server / http_mcp / mcp_client are intentionally NOT imported
# eagerly here. `python -m receiptbox.mcp_server` makes runpy execute this
# package first and then the module again as __main__; an eager import of the
# same submodule triggers the "found in sys.modules" RuntimeWarning and runs
# the module twice ("unpredictable behaviour"). Lazy attribute access keeps
# `from receiptbox import TOOLS` working without the double execution.
_LAZY = {
    "TOOLS": ("receiptbox.mcp_server", "TOOLS"),
    "handle_message": ("receiptbox.mcp_server", "handle_message"),
    "serve": ("receiptbox.mcp_server", "serve"),
    "PROTOCOL_VERSION": ("receiptbox.mcp_server", "PROTOCOL_VERSION"),
    "SUPPORTED_VERSIONS": ("receiptbox.mcp_server", "SUPPORTED_VERSIONS"),
    "make_http_server": ("receiptbox.http_mcp", "make_server"),
    "StreamableHttpClient": ("receiptbox.mcp_client", "StreamableHttpClient"),
}


def __getattr__(name):
    import importlib
    if name in ("mcp_server", "http_mcp", "mcp_client"):
        return importlib.import_module("receiptbox." + name)
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    mod = importlib.import_module(target[0])
    return getattr(mod, target[1])


__version__ = "1.1.0"
