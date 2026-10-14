"""ReceiptBox 风控引擎：结构化发票 → 风险发现 + 风险评分。

透明规则引擎（非黑箱）：每条发现都带 evidence（依据哪些字段/数字），
可解释、可复现、可核对。规则源自生产验证过的 InvoiceAudit 引擎。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from .models import Invoice, Finding, Report

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _cur_sym(code: str) -> str:
    return {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£", "INR": "₹"}.get(code, "¥")


def _money(x: Optional[float], code: str = "CNY") -> str:
    return "—" if x is None else f"{_cur_sym(code)}{x:,.2f}"


def audit(invoices: list, today: Optional[date] = None,
          brain: str = "offline") -> Report:
    today = today or date.today()
    rep = Report(invoices=invoices, brain=brain, created_on=today)
    findings: list = []

    def add(sev, code, label, inv, detail, advice, evidence=""):
        findings.append(Finding(sev, code, label, inv.label(), detail, advice, evidence))

    # 1) 逐票检查
    for inv in invoices:
        cur = inv.currency or "CNY"
        # 缺字段
        missing = [n for n, v in (("供应商", inv.vendor), ("票号", inv.invoice_no),
                                  ("日期", inv.date_str), ("总额", inv.total)) if not v]
        if missing:
            add("CAUTION", "MISSING_FIELD", "关键字段缺失", inv,
                f"缺少：{'、'.join(missing)}。字段不全的票难以入账与对账。",
                "补齐关键字段后再入账；可用 LLM 提取提升字段完整度。",
                evidence=f"检查字段 vendor/invoice_no/date_str/total，缺失 {missing}")

        # 算术：小计 + 税 = 总额
        if inv.subtotal is not None and inv.tax is not None and inv.total is not None:
            calc = inv.subtotal + inv.tax
            if abs(calc - inv.total) > 0.01:
                add("HIGH", "TOTAL_MISMATCH", "价税合计不平", inv,
                    f"小计 {_money(inv.subtotal, cur)} + 税额 {_money(inv.tax, cur)} = "
                    f"{_money(calc, cur)}，但票面总额 {_money(inv.total, cur)}，"
                    f"差 {_money(abs(calc - inv.total), cur)}。",
                    "算术对不上极可能开错或被改动，务必让供应商重新开具。",
                    evidence=f"subtotal={inv.subtotal} tax={inv.tax} total={inv.total}")

        # 税率复核
        if inv.subtotal is not None and inv.tax is not None and inv.subtotal > 0:
            implied = inv.tax / inv.subtotal
            if inv.tax_rate is not None and abs(implied - inv.tax_rate) > 0.005:
                expect = round(inv.subtotal * inv.tax_rate, 2)
                add("CAUTION", "TAX_MISMATCH", "税率与税额不符", inv,
                    f"按小计 {_money(inv.subtotal, cur)} × 税率 {inv.tax_rate:.0%} 税额应为 "
                    f"{_money(expect, cur)}，但票面 {_money(inv.tax, cur)}（实际约 {implied:.1%}）。",
                    "税率错会直接影响进项抵扣，建议重开。",
                    evidence=f"implied={implied:.4f} declared={inv.tax_rate}")

        # 整数大额
        if inv.total is not None and inv.total >= 10000 \
                and abs(inv.total - round(inv.total)) < 1e-9 and round(inv.total) % 1000 == 0:
            add("CAUTION", "ROUND_AMOUNT", "大额整数金额", inv,
                f"总额 {_money(inv.total, cur)} 是整齐的千位整数，真实业务较少见。",
                "确认是否为估价/框架结算，警惕凑整风险。",
                evidence=f"total={inv.total}")

        # 未来日期
        d = _parse_date(inv.date_str)
        if d and d > today:
            add("CAUTION", "FUTURE_DATE", "开票日期在未来", inv,
                f"开票日期 {inv.date_str} 晚于今天 {today.isoformat()}。",
                "未来日期的票不能提前入账，确认是否笔误。",
                evidence=f"date={inv.date_str} today={today.isoformat()}")

        # 陈旧未付
        if d and inv.status and inv.status.lower() in ("unpaid", "未付", "未支付", "待付"):
            age = (today - d).days
            if age > 180:
                add("CAUTION", "STALE_UNPAID", "超期未付", inv,
                    f"开票于 {inv.date_str}，已 {age} 天仍未付。",
                    "长期挂账易生重复付款或坏账，尽快结清或做坏账处理。",
                    evidence=f"age_days={age}")

    # 2) 跨票重复（双付风险，最高优先）
    seen: dict = {}
    for inv in invoices:
        if not inv.invoice_no and inv.total is None:
            continue
        key_no = (inv.vendor.lower(), inv.invoice_no.lower()) if inv.invoice_no else None
        key_amt = (inv.vendor.lower(), inv.total, _parse_date(inv.date_str)) \
            if inv.total is not None else None
        for key in (key_no, key_amt):
            if not key:
                continue
            if key in seen:
                first = seen[key]
                reason = "票号相同" if key is key_no else "同供应商同金额同日"
                add("HIGH", "DUPLICATE", "疑似重复发票（双付风险）", inv,
                    f"与「{first.label()}」{reason}，两笔都付=重复付款。",
                    "立即核对是否重复录入/请款，确认只付一次。",
                    evidence=f"dup_key={key}")
                break
            seen[key] = inv

    highs = sum(1 for f in findings if f.severity == "HIGH")
    cautions = sum(1 for f in findings if f.severity == "CAUTION")
    rep.findings = findings
    rep.score = min(100, highs * 30 + cautions * 10)
    rep.band = "HIGH" if rep.score >= 30 else ("MEDIUM" if rep.score >= 10 else "LOW")
    n = len(invoices)
    if not findings:
        rep.summary = f"审查 {n} 张发票，未发现明显风险。"
    else:
        rep.summary = (f"审查 {n} 张发票，发现 {highs} 个高风险、{cautions} 个待留意项，"
                       f"风险等级 {rep.band}。")
    return rep
