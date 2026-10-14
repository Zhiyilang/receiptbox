# DEPLOY.md — 把 ReceiptBox 发布出去

## A. Hugging Face Space（黑客松要求：项目发布为 HF Space）

1. 登录 https://huggingface.co → **New Space**：
   - Owner: 你的账号 · Space name: `receiptbox`
   - SDK: **Gradio** · Hardware: **CPU basic（免费）** · Visibility: **Public**
2. 本地克隆 Space 仓库并推送本目录内容：

   ```bash
   git clone https://huggingface.co/spaces/<你的账号>/receiptbox
   cp -r receiptbox/ app.py requirements.txt eval.py examples/ README.md LICENSE \
         <clone目录>/
   cd <clone目录> && git add . && git commit -m "ReceiptBox: invoice-audit MCP server" && git push
   ```
   （`app.py` 已读 `PORT` 环境变量并绑 `0.0.0.0`，HF 平台直接可用。）

3. **Settings → Variables and secrets**（可选，不配则离线大脑+演示深链）：
   - `RECEIPTBOX_LLM_BASE` / `RECEIPTBOX_LLM_KEY` / `RECEIPTBOX_LLM_MODEL`
   - `RECEIPTBOX_PAYPAL_CLIENT_ID` / `RECEIPTBOX_PAYPAL_SECRET`

4. 等构建完成（约 1-2 分钟），Space 页面出现 4 个工具标签页。
   MCP 端点：`https://<你的账号>-receiptbox.hf.space/gradio_api/mcp/`

5. **验证 MCP**（本地电脑，无需 Gradio）：

   ```bash
   pip install mcp
   python examples/mcp_client_demo.py https://<你的账号>-receiptbox.hf.space/gradio_api/mcp/
   ```

## B. 本地 / 任意云

```bash
pip install -r requirements.txt
python app.py        # 默认 7860，读 PORT 环境变量
```

## C. 只当 MCP server（无 Web）

```bash
python -m receiptbox.mcp_server     # stdio，JSON-RPC 2.0，一行一消息
```

接 Claude Desktop（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "receiptbox": {
      "command": "python",
      "args": ["-m", "receiptbox.mcp_server"],
      "cwd": "<receiptbox 目录绝对路径>"
    }
  }
}
```

## D. 健康检查

- `GET /` → 200（Gradio 落地页）
- `POST /gradio_api/mcp/` 带 `initialize` → SSE 返回 serverInfo
- `python eval.py` → 32/32（含真实子进程协议往返）
