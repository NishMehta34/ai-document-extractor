"""The fields we extract for each document type.

Numbers are cleaned before validation ("$1,234.50" -> 1234.5), because
models often copy the formatting they see in the document.
Dates are kept as strings here and checked in validation.py, so one
oddly formatted date flags a field instead of failing the whole document.
"""

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


def clean_number(value: Any) -> Any:
    """'$1,234.50' -> 1234.5, '(12.00)' -> -12.0, '' -> None."""
    if value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        negative = text.startswith("(") and text.endswith(")")
        text = re.sub(r"[^\d.,\-]", "", text)
        # European format: "1.234,50" or "234,50" -> decimal comma
        if re.search(r",\d{1,2}$", text) and (text.rfind(",") > text.rfind(".")):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
        if text in ("", "-", ".", "-."):
            return None
        number = float(text)
        return -abs(number) if negative else number
    return value


class _Cleaned(BaseModel):
    """Base model that cleans every float field before validation."""

    @field_validator("*", mode="before")
    @classmethod
    def _clean_numbers(cls, value, info):
        field = cls.model_fields[info.field_name]
        annotation = str(field.annotation)
        if "float" in annotation and not isinstance(value, list):
            return clean_number(value)
        if "str" in annotation and isinstance(value, (int, float)):
            return str(value)  # e.g. invoice number 1042 returned as a number
        if isinstance(value, str) and not value.strip():
            return None
        return value


class LineItem(_Cleaned):
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class Invoice(_Cleaned):
    invoice_number: str | None = Field(None, description="Invoice number or ID")
    invoice_date: str | None = Field(None, description="Date issued, as YYYY-MM-DD")
    due_date: str | None = Field(None, description="Payment due date, as YYYY-MM-DD")
    vendor_name: str | None = Field(None, description="Company that issued the invoice")
    vendor_address: str | None = None
    customer_name: str | None = Field(None, description="Company or person billed")
    currency: str | None = Field(None, description="ISO 4217 code, e.g. USD, GBP, EUR")
    subtotal: float | None = Field(None, description="Total before tax")
    tax: float | None = Field(None, description="Total tax amount")
    total: float | None = Field(None, description="Amount due including tax")
    line_items: list[LineItem] = Field(default_factory=list)


class PurchaseOrder(_Cleaned):
    po_number: str | None = Field(None, description="Purchase order number")
    order_date: str | None = Field(None, description="Order date, as YYYY-MM-DD")
    delivery_date: str | None = Field(None, description="Requested delivery date, as YYYY-MM-DD")
    buyer_name: str | None = Field(None, description="Company placing the order")
    supplier_name: str | None = Field(None, description="Company fulfilling the order")
    currency: str | None = Field(None, description="ISO 4217 code, e.g. USD, GBP, EUR")
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    line_items: list[LineItem] = Field(default_factory=list)


class Receipt(_Cleaned):
    merchant_name: str | None = None
    transaction_date: str | None = Field(None, description="Date of purchase, as YYYY-MM-DD")
    payment_method: str | None = Field(None, description="e.g. Visa, cash, PayPal")
    currency: str | None = Field(None, description="ISO 4217 code, e.g. USD, GBP, EUR")
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    line_items: list[LineItem] = Field(default_factory=list)


# doc_type -> (model, label, required fields)
DOC_TYPES: dict[str, tuple[type[BaseModel], str, list[str]]] = {
    "invoice": (Invoice, "Invoice", ["invoice_number", "vendor_name", "total"]),
    "purchase_order": (PurchaseOrder, "Purchase order", ["po_number", "buyer_name", "total"]),
    "receipt": (Receipt, "Receipt", ["merchant_name", "total"]),
}


def scalar_fields(model: type[BaseModel]) -> list[str]:
    """Top-level fields other than line_items, in schema order."""
    return [name for name in model.model_fields if name != "line_items"]
