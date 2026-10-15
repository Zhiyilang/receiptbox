# Friction log — Amazon Developer Hackathon (Alexa+ track)

> Required field: *"Friction log entries for each one: the task you attempted, the steps you
> took, what you expected vs. what actually happened, a severity rating, any workaround you
> used, and an actionable suggestion. Submissions with friction logs can earn up to a 10%
> judging bonus."*
>
> Every entry below was reproduced by me against the code in this repository. Entries 1 and 2
> are bugs **I found in my own server** and then fixed — I am logging them rather than quietly
> patching them, because both are places where the spec is easy to misread.

Severity scale: **Blocker** (cannot proceed) → **High** (wrong behaviour, real user impact) →
**Medium** (works, but costs time or surprises) → **Low** (polish / documentation).

---

## 1. Missing or invalid session returned 404 instead of 400 — and it was my bug

- **Task:** verify my Streamable HTTP server rejects requests that carry no
  `Mcp-Session-Id`.
- **Steps:** started `python -m receiptbox.http_mcp`, POSTed `tools/list` with no session
  header, then again after `DELETE`-ing a live session.
- **Expected:** `400 Bad Request` with a JSON-RPC error body (spec 2025-11-25, session
  management).
- **Actual:** `404 Not Found` — `{"error":{"code":-32000,...}}`. The body was right; the status
  code was wrong.
- **Severity:** **Medium.** A conformant client that branches on status code would treat 404 as
  "endpoint gone" and could tear down its connection instead of re-initialising.
- **Workaround / fix:** changed both paths (missing session, terminated session) to `400`, and
  pinned it with assertions `http.no-session-400` and `http.after-delete-400` so it cannot
  regress.
- **Suggestion for Amazon / the MCP spec:** publish a one-line status-code table for session
  errors (missing → 400, expired → 400, terminated → 400, unknown method → 200 + JSON-RPC
  error). The distinction between *transport* failures (HTTP status) and *protocol* failures
  (JSON-RPC error object) is the single easiest thing to get backwards when implementing this
  by hand, and it currently has to be inferred from prose.

## 2. A client asking for JSON only was still fed an SSE frame — also my bug

- **Task:** call `tools/list` with `Accept: application/json` (no `text/event-stream`).
- **Steps:** POST with `Accept: application/json` only.
- **Expected:** a single JSON object, `Content-Type: application/json`.
- **Actual:** `Content-Type: text/event-stream` with an `event: message\ndata: {...}` frame.
- **Severity:** **High.** This is the one that would genuinely break people. Any client that
  does `json.loads(response.body)` — which is most non-SDK integrations, and exactly what a
  first-time MCP integrator writes — gets a `JSONDecodeError` on a perfectly valid request.
  Streamable HTTP lets the server answer with either representation, so a server is
  "conformant" while still being unusable for that client.
- **Workaround / fix:** added content negotiation (`_wants_sse()`): SSE only when the client
  actually asks for `text/event-stream`; plain JSON otherwise. Assertions
  `http.json-accept-*` and `http.sse-accept-*` lock both branches, and the batch (array) path
  negotiates too.
- **Suggestion:** make content negotiation a *stated requirement with a conformance test*,
  not an option. A reference client in the docs that sends `Accept: application/json` only —
  and a conformance suite that asserts a bare-JSON response — would have caught this in minutes
  for every implementer.

## 3. Version negotiation echoes back older versions rather than upgrading

- **Task:** confirm `initialize` negotiates correctly across client versions.
- **Steps:** `initialize` with `protocolVersion` set to `2025-06-18`, then to `2030-01-01`.
- **Expected:** server responds with the newest version it supports.
- **Actual:** for `2030-01-01` it correctly answered `2025-11-25`. For `2025-06-18` it echoed
  `2025-06-18` back. That is defensible — the server does support that version — but a client
  cannot tell from the response whether it got *its* version echoed or the server's best
  version chosen.
- **Severity:** **Low.**
- **Workaround:** treat the returned `protocolVersion` as authoritative in my client
  (`mcp_client.py`) rather than assuming what I asked for.
- **Suggestion:** recommend that servers always return the *highest* version they support, or
  add a response field such as `negotiatedFrom` so the difference is explicit. Right now an
  echo and a negotiation look identical on the wire.

## 4. The server→client GET stream is not implemented (405)

- **Task:** open a `GET` on the MCP endpoint with `Accept: text/event-stream` to receive
  server-initiated messages.
- **Expected:** a long-lived SSE stream.
- **Actual:** `405` with a JSON-RPC `-32600` error.
- **Severity:** **Low** — the spec explicitly permits this, and ReceiptBox has no server-
  initiated notifications to send, so it is the right call for this project.
- **Workaround:** documented in the module docstring; the client never opens GET.
- **Suggestion:** none, the spec is clear. Noting it so a reviewer reading the endpoint does
  not think it was an oversight.

## 5. No Alexa+ hardware available to a developer outside the Preview regions

- **Task:** validate the real Alexa+ end-to-end experience (voice turn → MCP tool call → spoken
  reply) on an actual device.
- **Steps:** looked for a way to run an Alexa+ skill or register a self-hosted MCP server with
  a real Alexa+ endpoint from where I am; there is none — Alexa+ is Preview-only and I have no
  device.
- **Expected:** some path — even a simulator or a sandbox endpoint — to exercise the real
  assistant loop.
- **Actual:** none available, so I built `alexa_sim.py`: a web console that reproduces the
  Alexa+ interaction contract (`PERCEIVE → PLAN → MCP initialize → tools/call → EXPLAIN →
  RECEIPT`) against my real self-hosted MCP server over real HTTP. It degrades to offline when
  the server is unreachable so the demo never dies.
- **Severity:** **High** for any developer outside the Preview regions. This is the single
  biggest participation barrier in the whole track: a global hackathon with a device-gated
  primary experience.
- **Workaround:** the simulated-experience path the rules allow, plus a real MCP server behind
  it so the integration being judged is genuine, not mocked.
- **Suggestion:** ship an official Alexa+ **simulator** — a hosted endpoint that speaks the same
  MCP handshake and returns synthetic voice turns. It would (a) let non-US developers test the
  real contract, (b) give Amazon structured friction data from a far larger developer pool
  before GA, and (c) make the "self-hosted MCP server" requirement testable by everyone. Right
  now the only way to validate is to own hardware in a Preview region.

## 6. A local HTTP proxy silently ate my localhost MCP traffic

- **Task:** probe my own MCP server on `127.0.0.1` from a Python client.
- **Steps:** `urllib.request` POST to `http://127.0.0.1:<port>/mcp`.
- **Expected:** a response from my server.
- **Actual:** `502` with `upstream connect failed: 由于目标计算机积极拒绝` — the request went
  through the machine's configured HTTP proxy, which naturally has no route to my loopback
  port. `curl` worked (I passed `--noproxy '*'`), so for a while the server looked broken when
  it was not.
- **Severity:** **Medium**, but high time cost — I lost a full debugging cycle to it.
- **Workaround:** `urllib.request.build_opener(urllib.request.ProxyHandler({}))` in the probe
  client, and the same proxy-bypass in the eval suite so the tests never depend on machine
  proxy settings.
- **Suggestion:** mention it in the MCP getting-started docs. Developers behind corporate or
  regional proxies — a large share of a *global* hackathon's entrants — will hit this within
  their first ten minutes, and the failure mode points at the server rather than the proxy.
  One line ("if you get 502 on localhost, bypass your proxy") saves everyone an hour.

## 7. Hand-writing SigV4 for Bedrock without boto3

- **Task:** call the Bedrock Converse API with zero third-party dependencies (the whole project
  is stdlib-only).
- **Steps:** implemented AWS Signature V4 by hand in `receiptbox/aws_brain.py` — canonical
  request, credential scope, signed headers, payload hash.
- **Expected:** an authoritative reference implementation to check against.
- **Actual:** the pieces are documented, but there is no canonical *minimal* no-SDK example, so
  correctness had to be established by unit-testing the signature deterministically and
  asserting header shape.
- **Severity:** **Medium** — roughly an hour of the build.
- **Workaround:** pinned the signing with assertions (`aws.sigv4-deterministic`,
  `aws.sigv4-header`, `aws.amz-date-format`) so the function is provably stable.
- **Suggestion:** publish an official "SigV4 in 40 lines of standard library" walkthrough with
  a known-answer test vector. It would materially lower the floor for anyone who cannot or will
  not install an SDK.

## 8. Messy invoices break naive parsers in ways that look like success

- **Task:** extract subtotal / tax / total from real-world invoice text.
- **Steps:** ran the offline regex extractor over invoices with `$1,234.50`, `(89.00)` for
  refunds, multi-line line items, and mixed date formats.
- **Expected:** the numbers come out right.
- **Actual:** the failure mode is not a crash — it is a *plausible wrong number*. A thousands
  separator parsed as a decimal point, or a parenthesised amount read as positive, produces a
  confident total that is quietly wrong. That is far more dangerous than an exception.
- **Severity:** **High** for a product whose entire promise is "audit this before you pay it".
- **Workaround:** the extractor returns the matched evidence string alongside every number, so
  the audit layer can show *which characters* produced each field; arithmetic checks
  (subtotal + tax ≈ total) then catch the residue; duplicate detection uses normalised
  vendor + amount + date rather than raw text.
- **Suggestion:** this is really advice to whoever builds on top: never accept a parsed amount
  without its source substring. Echoing the evidence is what turns a wrong number into a
  visible one.

---

## Summary

| # | Area | Severity | Status |
|---|---|---|---|
| 1 | Session errors returned 404 instead of 400 | Medium | Fixed + asserted |
| 2 | JSON-only client was served an SSE frame | High | Fixed + asserted |
| 3 | Version negotiation echoes rather than upgrades | Low | Handled client-side |
| 4 | No server→client GET stream | Low | By design, documented |
| 5 | No Alexa+ hardware / no simulator outside Preview regions | **High** | Built `alexa_sim.py` |
| 6 | Local proxy swallowed localhost traffic | Medium | Proxy bypass in client + tests |
| 7 | No minimal no-SDK SigV4 reference | Medium | Assertions instead |
| 8 | Plausible-but-wrong amount parsing | **High** | Evidence strings + arithmetic checks |

`python eval.py` → **68 passed, 0 failed**, including 12 new assertions that pin entries 1–4 so
they cannot silently regress.
