"""AWS Bedrock 提取大脑 —— 纯标准库 SigV4 签名，零依赖。

这是 Amazon Developer Hackathon「AWS Builder Mini Challenge」的集成点：
有 AWS 凭据时，提取大脑走 Amazon Bedrock（Converse API）；
无凭据时上层自动回落离线正则大脑（见 extract.get_extractor）。

为什么手写 SigV4：项目铁律是零第三方依赖（任何机器 `python` 直接跑），
所以不引 boto3，用 hmac/sha256 按 AWS 签名 v4 规范实现。

环境变量：
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN(可选)
  AWS_REGION（默认 us-east-1）
  RECEIPTBOX_BEDROCK_MODEL（默认 anthropic.claude-3-haiku-20240307-v1:0）
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request

from .extract import Invoice, LLMExtractor, _normalize_date, _num

SERVICE = "bedrock-runtime"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signature_key(secret: str, date: str, region: str, service: str) -> bytes:
    """AWS Signature V4 派生签名密钥（规范 4 步）。"""
    k_date = _hmac(("AWS4" + secret).encode("utf-8"), date)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def signed_headers(method: str, url: str, region: str,
                   access_key: str, secret_key: str,
                   payload: bytes, session_token: str = None) -> dict:
    """构造带 Authorization 头的请求头（SigV4）。"""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    canonical_uri = parsed.path or "/"
    now = _dt.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = _sha256_hex(payload)

    headers = {
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    if session_token:
        headers["x-amz-security-token"] = session_token

    signed_names = ";".join(sorted(headers))
    canonical_headers = "".join(
        "%s:%s\n" % (k, headers[k]) for k in sorted(headers))
    canonical_request = "\n".join([
        method, canonical_uri, "", canonical_headers, signed_names, payload_hash,
    ])
    scope = "%s/%s/%s/aws4_request" % (date_stamp, region, SERVICE)
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])
    key = signature_key(secret_key, date_stamp, region, SERVICE)
    signature = hmac.new(key, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (access_key, scope, signed_names, signature))
    return headers


class BedrockExtractor(LLMExtractor):
    """Amazon Bedrock Converse API 提取器（接口与 LLMExtractor 一致）。"""

    name = "bedrock"

    def __init__(self, model: str, region: str, access_key: str,
                 secret_key: str, session_token: str = None,
                 timeout: int = 30) -> None:
        self.model = model
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.timeout = timeout
        self.base_url = ("https://bedrock-runtime.%s.amazonaws.com"
                         % region)

    def extract(self, text: str, index: int = 1) -> Invoice:
        body = json.dumps({
            "modelId": self.model,
            "system": [{"text": self.SYSTEM}],
            "messages": [{"role": "user",
                          "content": [{"text": text.strip()[:6000]}]}],
            "inferenceConfig": {"temperature": 0, "maxTokens": 2048},
        }).encode("utf-8")
        url = "%s/model/%s/converse" % (self.base_url, self.model)
        headers = signed_headers(
            "POST", url, self.region, self.access_key, self.secret_key,
            body, self.session_token)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["output"]["message"]["content"][0]["text"]
        data = json.loads(content[content.find("{"):content.rfind("}") + 1])
        return Invoice(
            raw=text.strip(), index=index, source=self.name, confidence=0.95,
            vendor=str(data.get("vendor") or ""),
            invoice_no=str(data.get("invoice_no") or ""),
            date_str=_normalize_date(str(data.get("date_str") or "")),
            due_str=_normalize_date(str(data.get("due_str") or "")),
            currency=str(data.get("currency") or "CNY"),
            subtotal=_num(data.get("subtotal")),
            tax_rate=_num(data.get("tax_rate")),
            tax=_num(data.get("tax")),
            total=_num(data.get("total")),
            status=str(data.get("status") or ""),
        )


def get_bedrock_extractor():
    """有 AWS 凭据才返回 BedrockExtractor，否则 None（上层回落）。"""
    import os
    key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not (key and secret):
        return None
    return BedrockExtractor(
        model=os.environ.get("RECEIPTBOX_BEDROCK_MODEL",
                             "anthropic.claude-3-haiku-20240307-v1:0"),
        region=os.environ.get("AWS_REGION", "us-east-1"),
        access_key=key, secret_key=secret,
        session_token=os.environ.get("AWS_SESSION_TOKEN"),
    )
