"""ReceiptBox 提取大脑：把"杂乱发票/收据文本"变成结构化 Invoice。

双大脑设计：
- OfflineExtractor：纯正则/启发式，零依赖、永远可用、确定性可复现。默认与兜底。
- LLMExtractor   ：OpenAI 兼容 /chat/completions，擅长 messy/扫描件转文字后的歧义场景。
                    需要 API Key；无 Key 或调用失败时由上层回落到离线大脑。

两个大脑产出同一份 Invoice 数据结构，MCP 工具/风控/回执无需关心数据来源。
另保留 parse_structured()：直接吃 KEY:VALUE 结构化块（多张票 --- 分隔）。
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from typing import Optional

from .models import Invoice


# ---------------------------------------------------------------------------
# 标签别名（中英）
# ---------------------------------------------------------------------------
_AMOUNT_LABELS = {
    "total": ("grand total", "amount due", "balance due", "total due", "total",
              "价税合计", "合计", "总额", "应付金额", "总金额", "实付"),
    "subtotal": ("subtotal", "sub-total", "net amount", "net", "小计", "不含税金额", "税前"),
    "tax": ("tax amount", "vat amount", "vat", "tax", "gst", "税额", "税金", "税费"),
    "tax_rate": ("tax rate", "vat rate", "rate of tax", "税率"),
}
_NO_LABELS = ("invoice no", "invoice number", "invoice #", "invoice id", "inv no",
              "bill no", "receipt no", "票号", "发票号", "发票编号", "编号", "单据号")
_VENDOR_LABELS = ("vendor", "supplier", "seller", "from", "bill from", "biller",
                  "merchant", "供应商", "卖方", "销方", "开票方", "商家")
_DATE_LABELS = ("invoice date", "issue date", "date of issue", "date issued", "date",
                "开票日期", "日期", "开票时间")
_DUE_LABELS = ("due date", "payment due", "pay by", "到期日", "付款截止", "截止日期", "账期")
_STATUS_PAID = ("paid", "已付", "已支付", "已结清", "settled")
_STATUS_UNPAID = ("unpaid", "未付", "未支付", "待付", "待支付", "outstanding")

_CURRENCY_SYMBOLS = {"$": "USD", "usd": "USD", "¥": "CNY", "rmb": "CNY", "cny": "CNY",
                     "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
                     "₹": "INR", "inr": "INR"}

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_NO_TOKEN_RE = re.compile(r"[A-Za-z]{0,4}[-/]?\d[\w/-]{2,}")

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
    "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%Y年%m月%d日",
)


def _to_float(text: str) -> Optional[float]:
    if not text:
        return None
    m = _NUM_RE.search(text.replace(",", ""))
    return float(m.group()) if m else None


def _to_rate(text: str) -> Optional[float]:
    if text is None:
        return None
    t = text.strip()
    if "%" in t:
        v = _to_float(t)
        return v / 100.0 if v is not None else None
    v = _to_float(t)
    if v is None:
        return None
    return v / 100.0 if v > 1.0 else v


def _normalize_date(s: str) -> str:
    """尽量归一化成 ISO；失败则原样返回（风控的 _parse_date 仍认多种格式）。"""
    if not s:
        return ""
    s = s.strip().strip(",;，。")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


_MONEY_RE = r"([$¥€£₹]|USD|RMB|CNY|EUR|GBP|INR)?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"


def _money_after_label(line: str):
    """取行内'标签对应的金额'。

    有冒号 → 取最后一个冒号后的第一个数字（避开 'VAT (13%): 1742.00' 里的 13）；
    无冒号 → 取整行最后一个'像钱'的数字（避开行内嵌的百分比）。
    返回 (数值, 货币符号或 None)。
    """
    if ":" in line or "：" in line:
        sep = "：" if ("：" in line and line.rfind("：") > line.rfind(":")) else ":"
        seg = line.rsplit(sep, 1)[1]
        nums = re.findall(_MONEY_RE, seg, re.I)
        target = nums[0] if nums else None
    else:
        nums = re.findall(_MONEY_RE, line, re.I)
        target = nums[-1] if nums else None
    if not target:
        return None
    sym = (target[0] or "").lower()
    cur = _CURRENCY_SYMBOLS.get(sym)
    try:
        val = float(target[1].replace(",", ""))
    except ValueError:
        return None
    return val, cur


def _find_labeled(lines: list, aliases: tuple) -> Optional[str]:
    """返回包含某别名的行的'值部分'（冒号后或别名后的文本）。"""
    for line in lines:
        low = line.lower()
        for a in aliases:
            idx = low.find(a.lower())
            if idx == -1:
                continue
            after = line[idx + len(a):]
            after = re.sub(r"^[\s:：#号]*", "", after)
            if after.strip():
                return after.strip()
    return None


def _detect_currency(text: str) -> str:
    for sym, code in (("$", "USD"), ("¥", "CNY"), ("€", "EUR"), ("£", "GBP"), ("₹", "INR")):
        if sym in text:
            return code
    low = text.lower()
    for word, code in (("usd", "USD"), ("cny", "CNY"), ("rmb", "CNY"),
                       ("eur", "EUR"), ("gbp", "GBP"), ("inr", "INR")):
        if word in low:
            return code
    return "CNY"


def _guess_vendor(lines: list) -> str:
    labeled = _find_labeled(lines, _VENDOR_LABELS)
    if labeled:
        return re.split(r"[\n,，]", labeled)[0].strip() or labeled.strip()
    company = re.compile(r"(公司|有限公司|集团|厂|店|Inc\.?|LLC|Ltd\.?|Co\.?|Corp|GmbH)", re.I)
    for line in lines[:6]:
        s = line.strip()
        if not s or ":" in s or "：" in s:
            continue
        if company.search(s) and len(s) <= 40:
            return s
    for line in lines[:3]:
        s = line.strip()
        if s and len(s) <= 40 and ":" not in s and "：" not in s:
            return s
    return ""


class OfflineExtractor:
    """离线正则/启发式提取器。零依赖、确定性、可复现。"""

    name = "offline"

    def extract(self, text: str, index: int = 1) -> Invoice:
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        inv = Invoice(raw=text.strip(), index=index, source=self.name, confidence=0.8)

        inv.vendor = _guess_vendor(lines)

        no_val = _find_labeled(lines, _NO_LABELS)
        if no_val:
            m = _NO_TOKEN_RE.search(no_val)
            inv.invoice_no = (m.group() if m else no_val.split()[0]).strip()

        d_val = _find_labeled(lines, _DATE_LABELS)
        if d_val:
            inv.date_str = _normalize_date(d_val)

        due_val = _find_labeled(lines, _DUE_LABELS)
        if due_val:
            inv.due_str = _normalize_date(due_val)

        # 金额：逐行找标签（带守卫，避开子串误判）
        #   total 跳过 subtotal 行（"Subtotal" 含子串 "total"）
        #   tax   跳过 tax-rate 行（"Tax Rate: 13%" 含子串 "tax"）
        _SKIP = {
            "total": re.compile(r"sub-?total|小计", re.I),
            "tax": re.compile(r"tax\s*rate|vat\s*rate|rate\s*of\s*tax|税率", re.I),
        }
        for field_name in ("subtotal", "tax", "total"):
            aliases = _AMOUNT_LABELS[field_name]
            skip = _SKIP.get(field_name)
            for line in lines:
                if skip and skip.search(line):
                    continue
                low = line.lower()
                if any(a.lower() in low for a in aliases):
                    mv = _money_after_label(line)
                    if mv and mv[0] is not None:
                        setattr(inv, field_name, mv[0])
                        if mv[1]:
                            inv.currency = mv[1]
                        break

        rate_val = _find_labeled(lines, _AMOUNT_LABELS["tax_rate"])
        if rate_val:
            inv.tax_rate = _to_rate(rate_val)

        inv.currency = _detect_currency(text) if inv.currency == "CNY" else inv.currency

        # 状态：先判未付再判已付（"unpaid" 含子串 "paid"）
        low_all = text.lower()
        if any(w in low_all for w in _STATUS_UNPAID):
            inv.status = "unpaid"
        elif any(w in low_all for w in _STATUS_PAID):
            inv.status = "paid"

        return inv


class LLMExtractor:
    """OpenAI 兼容 /chat/completions 提取器。需要 Key；失败由上层回落。"""

    name = "llm"

    SYSTEM = (
        "你是发票/收据字段抽取引擎。把用户给的票据文本抽取成 JSON，只输出 JSON，勿解释。"
        "字段：vendor, invoice_no, date_str(ISO 优先), due_str, currency(ISO 码), "
        "subtotal, tax_rate(小数如0.13), tax, total, status(paid/unpaid/其他原样)。"
        "抽不到就留空字符串或 null，严禁编造。"
    )

    def __init__(self, base_url: str, api_key: str, model: str = "gpt-4o-mini",
                 timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def extract(self, text: str, index: int = 1) -> Invoice:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": text.strip()[:6000]},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
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


def _num(v):
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_extractor():
    """提取大脑优先级：AWS Bedrock → OpenAI 兼容 LLM → 离线正则。
    环境变量：
      Bedrock:      AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
                     RECEIPTBOX_BEDROCK_MODEL
      OpenAI 兼容:  RECEIPTBOX_LLM_BASE / RECEIPTBOX_LLM_KEY / RECEIPTBOX_LLM_MODEL
    """
    import os
    try:
        from .aws_brain import get_bedrock_extractor
        bedrock = get_bedrock_extractor()
        if bedrock is not None:
            return bedrock
    except Exception:
        pass
    base = os.environ.get("RECEIPTBOX_LLM_BASE")
    key = os.environ.get("RECEIPTBOX_LLM_KEY")
    model = os.environ.get("RECEIPTBOX_LLM_MODEL", "gpt-4o-mini")
    if base and key:
        try:
            return LLMExtractor(base, key, model)
        except Exception:
            pass
    return OfflineExtractor()


# ---------------------------------------------------------------------------
# 结构化 KEY:VALUE 解析（多张票 --- 分隔）
# ---------------------------------------------------------------------------
_STRUCT_MAP = {
    "vendor": ("vendor", "supplier", "商家", "供应商", "卖方"),
    "invoice_no": ("invoice_no", "invoiceno", "invoice", "no", "number", "票号", "发票号", "编号"),
    "date_str": ("date", "date_str", "日期", "开票日期"),
    "due_str": ("due", "due_date", "到期日", "截止日期"),
    "subtotal": ("subtotal", "amount", "net", "小计", "金额", "不含税金额"),
    "tax_rate": ("tax_rate", "rate", "税率"),
    "tax": ("tax", "vat", "税额", "税金"),
    "total": ("total", "gross", "total_amount", "价税合计", "合计", "总额"),
    "status": ("status", "state", "状态"),
}


def _norm_key(k: str) -> str:
    return k.strip().lower().replace(" ", "_").replace("-", "_")


def parse_structured(text: str) -> list:
    """KEY:VALUE 块（--- 分隔）→ Invoice 列表。"""
    blocks = re.split(r"(?m)^\s*(?:---|===)\s*$", text)
    out = []
    idx = 0
    for block in blocks:
        if not block.strip():
            continue
        idx += 1
        inv = Invoice(raw=block.strip(), index=idx, source="structured", confidence=1.0)
        for line in block.splitlines():
            if ":" not in line and "：" not in line:
                continue
            sep = "：" if ("：" in line and (":" not in line or line.index("：") < line.index(":"))) else ":"
            key, _, val = line.partition(sep)
            nk = _norm_key(key)
            for attr, aliases in _STRUCT_MAP.items():
                if nk in aliases:
                    val = val.strip()
                    if attr in ("subtotal", "tax", "total"):
                        setattr(inv, attr, _to_float(val))
                    elif attr == "tax_rate":
                        setattr(inv, attr, _to_rate(val))
                    else:
                        setattr(inv, attr, val)
                    break
        out.append(inv)
    return out
