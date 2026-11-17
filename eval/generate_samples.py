"""Generate synthetic invoice PDFs with known correct answers (ground truth).

Three layouts (UK, US, EU) with different date formats, currencies,
number formats and quirks (no due date, zero tax, two-line addresses),
so the evaluation isn't just one template repeated.

    pip install -r requirements-dev.txt
    python eval/generate_samples.py          # writes eval/samples/*.pdf + *.json

All companies are fictional.
"""

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "samples"

VENDORS = [
    ("Northwind Analytics Ltd", "14 Carver Street\nSheffield S1 4FS"),
    ("Bluepeak Software Inc.", "2200 Mission Street, Suite 400\nSan Francisco, CA 94110"),
    ("Kestrel Data GmbH", "Lindenstrasse 18\n10969 Berlin"),
    ("Harbour & Finch Consulting", "3 Quay Road\nBristol BS1 6QA"),
    ("Orbital Cloud Services LLC", "88 Pine Avenue\nAustin, TX 78701"),
    ("Veldt Systems B.V.", "Keizersgracht 210\n1016 DZ Amsterdam"),
]
CUSTOMERS = ["Acme Logistics", "Brightline Retail", "Cobalt Health Group",
             "Dunmore Foods", "Evergreen Property Co", "Fable Media Ltd"]
ITEMS = [("Backend development (hours)", 45, 95), ("Data pipeline setup", 1, 2500),
         ("Cloud hosting - monthly", 1, 340), ("AI model integration", 1, 1800),
         ("QA testing (hours)", 30, 60), ("Project management (hours)", 20, 80),
         ("API maintenance retainer", 1, 900), ("Dashboard design", 1, 1200),
         ("Database migration", 1, 1650), ("Support hours", 10, 70)]

LAYOUTS = {
    "uk": {"currency": "GBP", "symbol": "£", "date": "%d/%m/%Y", "tax_rates": [0.2, 0.0],
           "thousands": ",", "decimal": ".", "number_label": "Invoice No:"},
    "us": {"currency": "USD", "symbol": "$", "date": "%B %-d, %Y", "tax_rates": [0.0825, 0.0, 0.06],
           "thousands": ",", "decimal": ".", "number_label": "Invoice #"},
    "eu": {"currency": "EUR", "symbol": "€", "date": "%Y-%m-%d", "tax_rates": [0.19, 0.21],
           "thousands": ".", "decimal": ",", "number_label": "Invoice Number:"},
}


def money(value: float, layout: dict) -> str:
    text = f"{value:,.2f}"
    if layout["thousands"] == ".":  # 1,234.50 -> 1.234,50
        text = text.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{layout['symbol']}{text}"


def make_invoice(n: int, rng: random.Random) -> dict:
    layout_name = ["uk", "us", "eu"][n % 3]
    layout = LAYOUTS[layout_name]
    vendor, address = rng.choice(VENDORS)
    issued = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    has_due = n % 5 != 4  # every fifth invoice has no due date
    due = issued + timedelta(days=rng.choice([14, 30, 45])) if has_due else None

    lines = []
    for desc, max_qty, base_price in rng.sample(ITEMS, rng.randint(1, 5)):
        qty = rng.randint(1, max_qty)
        price = round(base_price * rng.uniform(0.8, 1.2), 2)
        lines.append({"description": desc, "quantity": float(qty),
                      "unit_price": price, "amount": round(qty * price, 2)})
    subtotal = round(sum(l["amount"] for l in lines), 2)
    tax = round(subtotal * rng.choice(layout["tax_rates"]), 2)

    return {
        "layout": layout_name,
        "address_lines": address.split("\n"),
        "truth": {
            "invoice_number": f"{['INV', 'NW', 'BP'][n % 3]}-{2026}{n:04d}",
            "invoice_date": issued.isoformat(),
            "due_date": due.isoformat() if due else None,
            "vendor_name": vendor,
            "vendor_address": address.replace("\n", ", "),
            "customer_name": rng.choice(CUSTOMERS),
            "currency": layout["currency"],
            "subtotal": subtotal,
            "tax": tax,
            "total": round(subtotal + tax, 2),
            "line_items": lines,
        },
    }


def draw_pdf(path: Path, invoice: dict) -> None:
    t, layout = invoice["truth"], LAYOUTS[invoice["layout"]]
    fmt = lambda iso: date.fromisoformat(iso).strftime(layout["date"])  # noqa: E731
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 60

    c.setFont("Helvetica-Bold", 20)
    c.drawString(50, y, "INVOICE" if invoice["layout"] != "eu" else "Invoice / Rechnung")
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(width - 50, y, t["vendor_name"])
    c.setFont("Helvetica", 9)
    for i, line in enumerate(invoice["address_lines"]):
        c.drawRightString(width - 50, y - 14 - 12 * i, line)

    y -= 70
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"{layout['number_label']} {t['invoice_number']}")
    c.drawString(50, y - 15, f"Date: {fmt(t['invoice_date'])}")
    if t["due_date"]:
        c.drawString(50, y - 30, f"Due date: {fmt(t['due_date'])}")
    c.drawString(320, y, "Bill to:")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(320, y - 15, t["customer_name"])

    y -= 70
    c.setFont("Helvetica-Bold", 10)
    for x, label in [(50, "Description"), (330, "Qty"), (390, "Unit price"), (480, "Amount")]:
        c.drawString(x, y, label)
    c.line(50, y - 5, width - 50, y - 5)
    c.setFont("Helvetica", 10)
    for item in t["line_items"]:
        y -= 18
        c.drawString(50, y, item["description"])
        c.drawString(330, y, f"{item['quantity']:g}")
        c.drawString(390, y, money(item["unit_price"], layout))
        c.drawString(480, y, money(item["amount"], layout))

    y -= 30
    c.line(330, y + 12, width - 50, y + 12)
    rate = round(t["tax"] / t["subtotal"] * 100, 2) if t["subtotal"] else 0
    for label, value in [("Subtotal", t["subtotal"]), (f"Tax ({rate:g}%)", t["tax"]),
                         (f"Total due ({t['currency']})", t["total"])]:
        c.setFont("Helvetica-Bold" if label.startswith("Total") else "Helvetica", 10)
        c.drawString(330, y, label)
        c.drawString(480, y, money(value, layout))
        y -= 16

    c.setFont("Helvetica", 8)
    c.drawString(50, 60, "Thank you for your business. Payment by bank transfer.")
    c.save()


def main(count: int = 25) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)  # fixed seed: same samples every time
    for n in range(1, count + 1):
        invoice = make_invoice(n, rng)
        stem = OUT / f"invoice_{n:02d}"
        draw_pdf(stem.with_suffix(".pdf"), invoice)
        stem.with_suffix(".json").write_text(json.dumps(invoice["truth"], indent=2))
    print(f"Wrote {count} invoices to {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 25)
