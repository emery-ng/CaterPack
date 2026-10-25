import re
import pdfplumber
from io import BytesIO


# Matches lines like: "Catering Pasta Tray 1 $95.00 $95.00"
_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<qty>\d+)\s+\$[\d,]+\.\d{2}\s+\$[\d,]+\.\d{2}$"
)
# Fallback for lines with single spaces: "Item Name 2 $50.00 $100.00"
_LINE_RE_SINGLE = re.compile(
    r"^(?P<name>.+?)\s+(?P<qty>\d+)\s+\$[\d,]+\.\d{2}\s+\$[\d,]+\.\d{2}$"
)

# Lines that are definitely not invoice items
_SKIP_PREFIXES = (
    "Items ", "Subtotal", "Total", "Payments", "Page ", "View online",
    "Appearance Fee", "Delivery", "Gratuity", "Tip", "Tax", "Deposit",
    "Or open", "To view",
)


def parse_invoice(pdf_bytes: bytes) -> list[dict]:
    """
    Extract line-item names and quantities from a Square invoice PDF.

    Returns a list of dicts: [{"item": str, "quantity": int}, ...]
    Items that appear more than once (duplicate line entries) are summed.
    """
    items: dict[str, int] = {}

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if not line or any(line.startswith(p) for p in _SKIP_PREFIXES):
                    continue

                m = _LINE_RE.match(line) or _LINE_RE_SINGLE.match(line)
                if m:
                    name = m.group("name").strip()
                    qty = int(m.group("qty"))
                    items[name] = items.get(name, 0) + qty

    return [{"item": name, "quantity": qty} for name, qty in items.items()]
