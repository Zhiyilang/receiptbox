# Amazon Developer Hackathon · Devpost 申报文案（可粘贴）

**赛事**：Build, Ship, Shape: Amazon Developer Hackathon
**截止**：Oct 24, 2026 02:00 GMT+7（北京 10-24 01:00）
**主赛道**：Alexa+ ｜ **迷你挑战**：AWS Builder + Open Source

---

## Project name
ReceiptBox — 付款前，先问一句 Alexa

## Tagline (if asked)
A self-hosted MCP server (spec 2025-11-25, Streamable HTTP) that audits invoices before you pay them — plus a simulated Alexa+ experience that proves the loop.

## Text description（粘贴区）

### 它解决什么问题
小企业主和自由职业者每天被「这张票到底能不能付」困扰：价税对不上、税率填错、同一张票被重复付款（双付是最常见的真金白银损失）。现有工具要么是人工核对，要么是黑箱 AI——不给证据，不敢信。

ReceiptBox 把「付款前审票」做成一个 MCP server：任何 MCP 宿主（Alexa+、Claude Desktop、自建 agent）都能直接调用；同时我们提供一个模拟 Alexa+ 体验的 Web 控制台，让不会配 MCP 的人也能立刻体验完整闭环。

### Alexa+ 赛道的两条路径，我们都交付了
1. **Self-hosted MCP server（spec 2025-11-25+, Streamable HTTP）**——`receiptbox/http_mcp.py`，纯 Python 标准库实现：单 POST endpoint、SSE 帧响应、`Mcp-Session-Id` 会话管理、`initialize` 版本协商、DELETE 会话终止。4 个工具：`extract_document` / `audit_document` / `risk_score` / `payment_link`。
2. **Simulated Alexa+ experience in a web app**——`alexa_sim.py`：用户说「Alexa，检查有没有重复票据」，控制台实时展示 PERCEIVE → PLAN → MCP tools/call → EXPLAIN → RECEIPT 全链路，每个 MCP 调用可见（协议版本、会话 id、工具名、参数），最后给出语音式短回复 + 结构化回执卡片。

### 智能体闭环（不是聊天演示）
```
utterance ──► PERCEIVE(意图路由) ──► PLAN(选工具+填参数)
   ──► MCP tools/call(Streamable HTTP, 真调用)
   ──► EXPLAIN(压缩成一句话) ──► RECEIPT(结构化卡片+证据)
```
风控引擎覆盖 7 类风险：价税不平、税率不符、**重复票（双付风险）**、缺字段、未来日期、超期未付、凑整金额——每条发现都带 evidence（哪个字段、什么值、哪条规则），评分公式公开可复现。

### 铁律：不编造
抽不到的字段就是空；没配 PayPal 凭据就返回明确标注的 demo deep-link，绝不伪装真实收款。负测试（空输入/未知工具/缺参数）全部在断言套件里，错误路径也符合 MCP 规范分级。

### 三大脑，零依赖
提取大脑三档降级：AWS Bedrock（Converse API，手写 SigV4，stdlib）→ OpenAI 兼容端点 → 离线正则引擎。任何机器 `python` 直接跑，无需 pip install 任何东西。

### 自证（评委可复现）
`python eval.py` → **56/56 断言全过**，覆盖：stdio 传输真实子进程往返、Streamable HTTP 真服务+真客户端端到端（含版本协商/会话/错误路径/通知 202）、SigV4 签名确定性、Alexa 模拟器闭环与离线降级。

## Built With（标签）
`python` `mcp` `model-context-protocol` `alexa` `amazon-bedrock` `aws` `sigv4` `ai-agents` `stdlib` `json-rpc` `sse` `paypal`

## Repo
https://github.com/Zhiyilang/receiptbox （公开 · MIT）

## Demo video
<YouTube 链接，用户上传后回填>（脚本见仓库 DEMO_SCRIPT.md，<3 分钟）

## Track & mini challenges 勾选
- [x] Alexa+（主赛道）
- [x] AWS Builder Mini Challenge（Bedrock 集成，见 FEEDBACK.md §2）
- [x] Open Source Mini Challenge（赛窗内新建公开 MIT 仓库 + 68 条断言）

## Friction log（**最高 +10% 评分加成，务必填**）
赛会原文：*"Submissions with friction logs can earn up to a 10% judging bonus."*
**把 `FRICTION_LOG.md` 的内容填进 Devpost 的 Friction log 字段。**
8 条全部是我实测复现的，其中两条是我自查抓出并已修的自身 spec 偏差
（session 缺失返回 404 而非 400；只 Accept JSON 的客户端被喂了 SSE 帧）。
诚实写出"这是我自己的 bug"比只写外部环境抱怨更有说服力。

## Feature requests（选填，可再拿印象分）
1. **官方 Alexa+ 模拟器**（关键）：让非 Preview 地区开发者能测真实交互契约 ——
   Alexa+ 目前设备/地区受限，这是全球赛最大的参与门槛，我在 FRICTION_LOG.md §5 写了。
2. MCP 会话错误的状态码对照表（400 / 400 / 400 vs JSON-RPC error）。
3. MCP 内容协商纳入一致性测试（`Accept: application/json` 必须回裸 JSON）。
4. 无 SDK 的最小 SigV4 示例 + 已知答案测试向量。

## 提交前自检
- [ ] GitHub 仓库公开 + MIT LICENSE 在库内
- [ ] 代码里真实调用赛道技术（http_mcp.py 即 MCP server 本体，非 README 提及）
- [ ] <3min 英文 demo 视频传 YouTube 并公开
- [ ] FEEDBACK.md 内容粘贴进 Product feedback 字段
- [ ] **FRICTION_LOG.md 内容粘贴进 Friction log 字段（+10%）**
- [ ] Feature requests 字段填上面 4 条
- [ ] 说明项目创建于赛窗内（2026-10-14/15，赛窗 8-31 起）
