import math
import pandas as pd

# UOMs that represent discrete physical items — always round UP.
# Normalised to lowercase for comparison.
COUNTABLE_UOMS: frozenset[str] = frozenset({
    "ea", "each", "count", "ct", "pc", "pcs", "piece", "pieces",
    "box", "boxes", "container", "lid", "lids", "tray", "pan",
    "bag", "roll", "bottle", "jar", "pack", "packet",
})


def is_countable(uom: str) -> bool:
    return uom.strip().lower() in COUNTABLE_UOMS


def _round_for_uom(qty: float, uom: str) -> float:
    if is_countable(uom):
        return float(math.ceil(qty))   # never short-change on physical units
    return round(qty, 2)               # weight/volume — keep precision


def apply_rounding(df: pd.DataFrame) -> pd.DataFrame:
    """Add exact_qty, pack_qty, and qty_was_rounded columns.

    exact_qty   — raw calculated gross quantity (may be fractional)
    pack_qty    — quantity staff should actually pack:
                    countable UOMs → ceil to whole number
                    weight/volume  → rounded to 2 decimal places
    qty_was_rounded — True when pack_qty differs from exact_qty
    """
    df = df.copy()
    df["exact_qty"] = df["total_gross_qty"]
    df["pack_qty"] = df.apply(
        lambda r: _round_for_uom(r["total_gross_qty"], r["uom"]), axis=1
    )
    df["qty_was_rounded"] = df["pack_qty"] != df["exact_qty"]
    return df
