# 产品反馈（Amazon Developer Hackathon 提交项）

> 赛会要求："Product feedback on every tool, API, or SDK you used: what you
> used it for, what worked well, what needs work, how onboarding felt, and
> whether you'd build with it again."

## 1. Alexa+ 开发者平台 / MCP 规范（spec 2025-11-25, Streamable HTTP）

**用途**：ReceiptBox 的核心交付物是一个 self-hosted MCP server（`receiptbox/http_mcp.py`，零依赖纯标准库），4 个工具（extract / audit / risk_score / payment_link）+ 一个模拟 Alexa+ 体验的 Web 控制台（`alexa_sim.py`）。

**做得好的**：
- Streamable HTTP 传输模型非常干净——单个 POST endpoint + SSE 帧响应 + `Mcp-Session-Id` 会话头，用 Python 标准库 200 行内就能实现，不需要任何 SDK。
- 工具即 JSON Schema 的发现机制（tools/list）让「agent 自己读说明书」成为可能，我们的模拟器就是靠它做自主规划的。
- 规范对「通知无响应」和「错误分级」（协议错误 vs isError 业务失败）的规定明确，实现时不用猜。

**待改进**：
- `initialize` 版本协商的规范表述偏紧：客户端请求服务端不支持的版本时，服务端应返回自己支持的最新版本——建议在规范示例里给一个明确的协商代码片段，减少实现分歧。
- 会话生命周期（DELETE 终止 / 会话过期）只有一句描述，建议补充服务端推荐的最大会话时长与过期后客户端行为（重连 vs 报错）。
- Streamable HTTP 下批量请求（JSON 数组）的响应帧格式建议给出权威示例。

** onboarding 感受**：作为 AlexOS 生态新手，MCP 规范文档 + 一个 curl 就能跑通第一个工具调用，入门曲线很平。会再次基于它构建。

## 2. AWS Builder Mini Challenge：Amazon Bedrock（Converse API，stdlib SigV4）

**用途**：ReceiptBox 的提取大脑三档优先级——有 AWS 凭据时走 Bedrock（`receiptbox/aws_brain.py`，手写 SigV4，`anthropic.claude-3-haiku`），无凭据时回落 OpenAI 兼容端点，再回落离线正则引擎。全部零第三方依赖。

**做得好的**：
- Converse API 的消息格式（system 数组 + content blocks）比裸文本补全更严谨，做字段抽取这种结构化任务时输出更稳。
- Bedrock 的 IAM 鉴权模型（SigV4）虽然要手写，但一旦实现就是纯函数，可完整离线单测——我们把签名确定性与头部构造都写进了断言套件（68/68 通过）。

**待改进**：
- 无 boto3 环境下手写 SigV4 的入门成本偏高：官方缺少一个「最小 SigV4 签名示例（Python 标准库版）」的权威参考，社区实现质量参差。建议补一篇 canonical walkthrough。
- Converse 的 `maxTokens` 默认值在不同模型间行为不一致，建议在 API 参考里明确每模型默认值。

** onboarding 感受**：模型调用本身 10 分钟跑通； SigV4 花了约 1 小时。会再次使用（并继续用 stdlib 直连）。

## 3. GitHub（代码仓库与评审协作）

**用途**：公开 MIT 仓库承载全部源码、断言套件（68 条）、三种传输的启动方式与文档。

**做得好的**：公开仓库 + MIT 的评审路径零摩擦，评委 clone 即可运行 `python eval.py` 自证。

**待改进**：本次未使用私有仓库 + collaborator 路径（选了公开 MIT 冲 Open Source Mini Challenge），无更多反馈。

**会再次构建吗**：会。整个项目（3 种 MCP 传输 + 模拟器 + 68 条断言）从零到提交只用了一天，其中 MCP + Bedrock 双集成各约 2 小时。
