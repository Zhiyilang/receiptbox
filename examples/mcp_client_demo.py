"""用官方 MCP Python SDK 消费 ReceiptBox 的 HTTP MCP 端点。

用法：
    pip install mcp
    python examples/mcp_client_demo.py [MCP_URL]

默认连本地 http://127.0.0.1:7860/gradio_api/mcp/；
部署到 HF Space 后传 https://<user>-receiptbox.hf.space/gradio_api/mcp/

这个脚本同时是「Track 1 functioning MCP server」的活证据：
任何标准 MCP 客户端都能发现并调用全部 4 个工具。
"""

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7860/gradio_api/mcp/"

MESSY = """Acme Office Supplies Co., Ltd.
Invoice No: INV-2026-0901
Invoice Date: 2026-09-15
Subtotal: 13400.00
Tax Rate: 13%
VAT: 1742.00
Total: 15142.00
Status: unpaid"""

BAD = """Vendor: Shenzhen XinDa Trading Co., Ltd.
Invoice No: XD-2026-0042
Invoice Date: 2026-01-20
Subtotal: 8800.00
Tax Rate: 13%
Tax: 500.00
Total: 9300.00
Status: unpaid"""


def show(title, text, limit=400):
    print("== %s ==" % title)
    print(text[:limit] + ("..." if len(text) > limit else ""))
    print()


async def main():
    print("connecting to", URL)
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, "| protocol:", init.protocolVersion)
            print()

            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])
            print()

            r = await session.call_tool("extract_document", {"text": MESSY})
            show("extract_document", r.content[0].text)

            r = await session.call_tool("risk_score", {"text": BAD})
            show("risk_score (bad invoice)", r.content[0].text)

            r = await session.call_tool("payment_link", {"text": MESSY})
            show("payment_link", r.content[0].text)

            # 错误路径：空输入 → isError=True，原因可读
            r = await session.call_tool("risk_score", {"text": ""})
            print("== error path ==")
            print("isError:", r.isError)
            print(r.content[0].text[:200])


asyncio.run(main())
