"""ReceiptBox 数据模型（零依赖，纯标准库）。

一张发票/收据的结构化表示 + 一条风控发现 + 一份审查报告。
MCP 工具的输入输出、风控引擎、回执渲染全部共享这份结构，
因此「哪个大脑抽的数据」对上层完全透明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Invoice:
    raw: str
    vendor: str = ""
    invoice_no: str = ""
    date_str: str = ""
    due_str: str = ""
    currency: str = "CNY"
    subtotal: Optional[float] = None
    tax_rate: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    status: str = ""
    index: int = 0
    # 溯源：由哪个大脑抽出、置信度如何（回执里给出证据）
    source: str = "offline"        # offline | llm
    confidence: float = 1.0

    def label(self) -> str:
        no = self.invoice_no or f"#{self.index}"
        return f"{self.vendor or '未知供应商'} · {no}"

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor, "invoice_no": self.invoice_no,
            "date_str": self.date_str, "due_str": self.due_str,
            "currency": self.currency, "subtotal": self.subtotal,
            "tax_rate": self.tax_rate, "tax": self.tax,
            "total": self.total, "status": self.status,
            "source": self.source, "confidence": self.confidence,
        }


@dataclass
class Finding:
    severity: str          # HIGH / CAUTION / INFO
    code: str
    label: str
    invoice: str
    detail: str
    advice: str
    evidence: str = ""     # 依据哪些字段/数字得出（可解释性）


@dataclass
class Report:
    invoices: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    score: int = 0
    band: str = "LOW"
    summary: str = ""
    brain: str = "offline"          # 本次审查实际使用的提取大脑
    created_on: Optional[date] = None

    def to_dict(self) -> dict:
        return {
            "summary": self.summary, "score": self.score, "band": self.band,
            "brain": self.brain,
            "invoice_count": len(self.invoices),
            "findings": [
                {"severity": f.severity, "code": f.code, "label": f.label,
                 "invoice": f.invoice, "detail": f.detail,
                 "advice": f.advice, "evidence": f.evidence}
                for f in self.findings
            ],
        }
