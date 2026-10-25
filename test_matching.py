"""
Run the full pipeline on every PDF in demo/ and print a diagnostic report.
Save one packing_list_<name>.xlsx per invoice to output/.

Usage (from project root):
    python test_matching.py
"""

from pathlib import Path

import pandas as pd

from utils.invoice_parser import parse_invoice
from utils.bom_processor import load_bom
from utils.config_loader import load_rules
from utils.packing_generator import generate_packing_list
from utils.excel_export import export_to_excel

DATA_DIR   = Path("demo")
OUTPUT_DIR = Path("output")
BOM_FILE   = DATA_DIR / "demo_BOM.xlsx"
W          = 70   # output width


def _section(label: str) -> str:
    return f"\n{f'[ {label} ] ':─<{W}}"


def _print_warnings(warnings: list[str], label: str) -> None:
    print(_section(label))
    if not warnings:
        print("  (none)")
        return
    for w in warnings:
        tag, _, body = w.partition("  ")
        print(f"  ⚠  {tag.strip()}")
        # wrap body at ~65 chars
        words, line = body.strip().split(), ""
        for word in words:
            if len(line) + len(word) + 1 > 65:
                print(f"       {line}")
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            print(f"       {line}")


def run_invoice(pdf_path: Path, bom_df, rules):
    print(f"\n{'═' * W}")
    print(f"  INVOICE: {pdf_path.name}")
    print(f"{'═' * W}")

    # ── 1. Parse ──────────────────────────────────────────────────────────────
    invoice_items = parse_invoice(pdf_path.read_bytes())
    print(_section("1  Extracted invoice items"))
    if not invoice_items:
        print("  ⚠  No items found — check PDF format.")
        return
    for item in invoice_items:
        print(f"  {item['quantity']:>4}×  {item['item']}")

    # ── 2. Match ──────────────────────────────────────────────────────────────
    result = generate_packing_list(invoice_items, bom_df, rules)

    print(_section("2  BOM matches  (recipe  ×multiplier)"))
    for item in invoice_items:
        name  = item["item"]
        match = result.matches.get(name)
        if name in result.skipped:
            status = "○  SKIPPED (rule)"
        elif match is not None:
            bom, mult = match
            mult_str = f"{mult:.4g}" if mult != int(mult) else str(int(mult))
            status = f"→  {bom}  ×{mult_str}"
        else:
            status = "✗  NO MATCH"
        print(f"  {name!r:50s}  {status}")

    # ── 3. Skipped ────────────────────────────────────────────────────────────
    print(_section("3  Skipped items"))
    if result.skipped:
        for s in result.skipped:
            print(f"  ○  {s}")
    else:
        print("  (none)")

    # ── 4. Unmatched ──────────────────────────────────────────────────────────
    print(_section("4  Unmatched items"))
    if result.unmatched:
        for u in result.unmatched:
            print(f"  ✗  {u}")
        print(f"\n  → To fix: add an alias in ALIAS_MAP (packing_generator.py)")
        print(  f"            or a skip rule in config/item_rules.csv")
    else:
        print("  (none — all non-skipped items matched)")

    # ── 5. Warnings ───────────────────────────────────────────────────────────
    _print_warnings(result.warnings, "5  Warnings")

    # ── 6. Packing list preview (grouped by dish) ─────────────────────────────
    print(_section("6  Packing list preview"))

    def _print_table(df, indent="    "):
        hdr = (f"{indent}{'Ingredient':<44}  {'UOM':<7}  "
               f"{'Exact Qty':>10}  {'Pack Qty':>9}  ↑")
        print(hdr)
        print(indent + "·" * 78)
        for _, row in df.iterrows():
            large   = row.get("total_net_qty", row.get("exact_qty", 0)) > 1000
            rounded = row.get("qty_was_rounded", False)
            warn    = "⚠ " if large   else "  "
            up      = " ⬆" if rounded else "  "
            ing     = str(row["ingredient"])[:44]
            exact   = row.get("exact_qty", row.get("total_gross_qty", 0))
            pack    = row.get("pack_qty",  exact)
            print(f"{indent}{warn}{ing:<44}  {str(row['uom']):<7}  "
                  f"{exact:>10.2f}  {pack:>9.2f}{up}")

    if not result.sections and result.df.empty:
        print("  (empty)")
    else:
        for sec in result.sections:
            mult_str = (f"{sec.multiplier:.4g}"
                        if sec.multiplier != int(sec.multiplier)
                        else str(int(sec.multiplier)))
            print(f"\n  ▸ {sec.invoice_item}  ×{mult_str}  →  {sec.bom_recipe}")
            _print_table(sec.df)

        n_rounded = int(result.df["qty_was_rounded"].sum()) if "qty_was_rounded" in result.df.columns else 0
        print(f"\n  ── GRAND TOTAL  ({len(result.df)} rows, {n_rounded} rounded up) ──")
        _print_table(result.df)

    # ── Save Excel ────────────────────────────────────────────────────────────
    stem     = pdf_path.stem.replace("sample_", "")
    out_path = OUTPUT_DIR / f"packing_list_{stem}.xlsx"
    out_path.write_bytes(export_to_excel(result))
    print(f"\n  Saved → {out_path}")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"\n{'─' * W}")
    print("  Loading BOM and rules…")
    bom_df = load_bom(BOM_FILE.read_bytes())
    rules  = load_rules()
    print(f"  BOM   : {len(bom_df)} ingredient rows, {bom_df['recipe'].nunique()} recipes")
    print(f"  Rules : {len(rules)} item rules loaded from config/item_rules.csv")

    pdfs = sorted(DATA_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {DATA_DIR}/")
        return

    for pdf_path in pdfs:
        run_invoice(pdf_path, bom_df, rules)

    print(f"\n{'─' * W}")
    print("  Done.")
    print(f"{'─' * W}\n")


if __name__ == "__main__":
    main()
