# DEMO_SCRIPT.md — ReceiptBox 演示视频分镜

> 录制工具：OBS / 系统自带录屏。两个本地进程：
> ```bash
> python -m receiptbox.http_mcp            # 终端 1：MCP server @ :7861
> PORT=7862 python alexa_sim.py            # 终端 2：模拟 Alexa+ @ :7862
> ```
> 另开浏览器 http://localhost:7862。全程 2:30–3:00（亚马逊上限 3 分钟）。

---

## 用途 A：Amazon Developer Hackathon（Alexa+ 赛道，<3min）

### 0:00–0:20 · 钩子
「付款之前，票是对的吗？小计 13400、税率 13%、 VAT 1742、总额 15142——
对得上。但如果是重复票，或者总额算错，肉眼真不一定看得出来。
ReceiptBox 是一个 self-hosted MCP server，spec 2025-11-25，
Streamable HTTP——在付款前把票审一遍。」

### 0:20–0:55 · 模拟 Alexa+ 体验（主画面，浏览器 :7862）
1. 左侧「设备」面板 + 右侧发票文本框（预填 messy invoice）。
2. 输入框键入「Alexa，检查有没有重复票据」→ 回车。
3. 高亮右侧步骤时间线依次点亮：
   `PERCEIVE → PLAN → MCP initialize (protocol 2025-11-25, session 65d4…) → tools/call audit_document → EXPLAIN → RECEIPT`。
4. 左侧语音回复气泡出现：「小心，我发现 1 组重复票据，可能是重复付款。
   建议先人工确认再付。」+ 绿色回执卡片。
   「每一个 MCP 调用都实时可见——协议版本、会话、工具名、参数。
   这不是聊天演示，这是真调用。」

### 0:55–1:25 · MCP server 本体（终端 1 + curl）
1. 终端 1 展示启动横幅：`ReceiptBox MCP (Streamable HTTP, spec 2025-11-25) on http://0.0.0.0:7861/mcp` + 4 个工具名。
2. curl `initialize` → 高亮响应里的 `protocolVersion: 2025-11-25` 和
   `Mcp-Session-Id` 响应头（「会话由服务端分配，后续请求必须带回」）。
3. curl `tools/call audit_document`（贴重复票）→ 高亮 `DUPLICATE` 发现
   和 `evidence` 字段。
   「200 行纯标准库，没有 gradio、没有 mcp SDK、没有 boto3——
   任何 MCP 宿主都能连。」

### 1:25–1:50 · AWS Builder 迷你挑战（Bedrock 三档大脑）
1. 展示 `receiptbox/aws_brain.py` 的 `signature_key` 函数：
   「为了零依赖，SigV4 是我们手写的——hmac + sha256，四步派生。」
2. `python eval.py` 尾部：`RESULT: 56 passed, 0 failed` 高亮 3 秒。
   「56 条断言：stdio 真实子进程往返、HTTP 真服务+真客户端端到端、
   版本协商、SigV4 确定性、模拟器离线降级——全部可复现。」

### 1:50–2:10 · 设计亮点（语速放慢，评委记分点）
- 「**一条工具层，三种传输**：stdio（Claude Desktop）、Streamable HTTP
  （Alexa+/任何宿主）、Gradio Web。同一份逻辑，不会两副面孔。」
- 「**铁律不编造**：抽不到的字段就是空；没 PayPal 凭据就返回明确标注的
  demo 链接，绝不伪装真实收款。」
- 「**永不崩**：MCP 不可达时模拟器自动降级进程内直调，标注 offline——
  演示不会因为网络死掉。」

### 2:10–2:30 · 收尾
「付款前，先问一句 Alexa。ReceiptBox——self-hosted MCP server，
MIT 开源，`python eval.py` 56/56 自证。GitHub: Zhiyilang/receiptbox。谢谢！」

---

## 用途 B：Hugging Face Gradio Hackathon（Track 1，1–5min）

> 若投 HF：改用 `python app.py`（:7860，Gradio Blocks + `/gradio_api/mcp/`），
> 分镜在 A 的基础上把「MCP server 本体」一段换成「协议透传标签页」演示
> （浏览器内直接发 JSON-RPC），并把 eval 32/32 的说法改成当前总数。

---

## 录制 checklist
- [ ] 浏览器 100% 缩放，隐藏书签栏；先跑通一遍再录
- [ ] 终端字体 ≥14pt，`python -u` 无缓冲
- [ ] curl 命令提前存成脚本，录制时一键粘贴（避免手打出错）
- [ ] 重复票样本用两行 `invoice_no: INV-1` 的 KEY:VALUE 块（--- 分隔）
- [ ] 视频 <3:00（亚马逊上限），上传 YouTube 后公开链接回填 Devpost
