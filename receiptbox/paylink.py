"""ReceiptBox 收款链接：给未付发票生成 PayPal 催收/收款链接。

双模式：
- live：配置了 PayPal 沙盒凭据（RECEIPTBOX_PAYPAL_CLIENT_ID/SECRET）时，
        走真实 Orders v2 API 创建订单并返回 approve 链接。
- demo：无凭据时返回确定性的沙盒深链（sb- 前缀业务邮箱），明确标注 demo，
        绝不伪装成真实收款入口。

链接永远可从 receipt 里点击；evidence 字段说明本次是哪种模式。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus


@dataclass
class PayLink:
    url: str
    mode: str            # live | demo
    evidence: str


_DEMO_BUSINESS = "sb-receiptbox-demo@business.example"


def _demo_link(total: float, currency: str, invoice_no: str, vendor: str) -> PayLink:
    item = "Invoice %s" % (invoice_no or "payment")
    if vendor:
        item += " - %s" % vendor
    url = ("https://www.sandbox.paypal.com/cgi-bin/webscr?cmd=_xclick"
           "&business=%s&amount=%.2f&currency_code=%s&item_name=%s"
           % (_DEMO_BUSINESS, total, currency.upper(), quote_plus(item)))
    return PayLink(
        url=url, mode="demo",
        evidence=("demo mode: no PayPal credentials configured "
                  "(set RECEIPTBOX_PAYPAL_CLIENT_ID/SECRET for live orders); "
                  "deterministic sandbox deep-link, not a real charge"),
    )


def _live_link(total: float, currency: str, invoice_no: str, vendor: str,
               client_id: str, secret: str) -> Optional[PayLink]:
    """走 PayPal Orders v2；任何失败都返回 None 让上层降级 demo。"""
    try:
        auth = base64.b64encode(("%s:%s" % (client_id, secret)).encode()).decode()
        body = json.dumps({
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": currency.upper(), "value": "%.2f" % total},
                "description": ("Invoice %s%s" % (invoice_no or "",
                                                  " - %s" % vendor if vendor else "")),
            }],
        }).encode()
        req = urllib.request.Request(
            "https://api-m.sandbox.paypal.com/v2/checkout/orders",
            data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Basic " + auth},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            order = json.loads(resp.read().decode())
        for link in order.get("links", []):
            if link.get("rel") == "approve":
                return PayLink(url=link["href"], mode="live",
                               evidence="live mode: PayPal sandbox Orders v2 order %s"
                                        % order.get("id", ""))
    except Exception:
        return None
    return None


def payment_link(total: float, currency: str = "USD", invoice_no: str = "",
                 vendor: str = "") -> PayLink:
    if total is None or total <= 0:
        raise ValueError("amount must be positive")
    client_id = os.environ.get("RECEIPTBOX_PAYPAL_CLIENT_ID")
    secret = os.environ.get("RECEIPTBOX_PAYPAL_SECRET")
    if client_id and secret:
        live = _live_link(total, currency, invoice_no, vendor, client_id, secret)
        if live:
            return live
    return _demo_link(total, currency, invoice_no, vendor)
