import re
from dataclasses import dataclass, field

import pandas as pd

from utils.config_loader import (
    ItemRule,
    LARGE_QTY_THRESHOLD,
    GUEST_COUNT_WARNING_THRESHOLD,
    get_rule,
    infer_bom_unit_size,
    load_rules,
)
from utils.rounding import apply_rounding

# Invoice names that don't follow any predictable pattern relative to BOM names.
# Keys are lowercased invoice item names; values are exact BOM recipe names.
ALIAS_MAP: dict[str, str] = {
    "green goodness bowl": "Catering Garden Salad Regular",
}


@dataclass
class RecipeSection:
    """Scaled + rounded ingredients for one matched invoice item."""
    invoice_item: str    # e.g. "Garden Salad"
    bom_recipe: str      # e.g. "Catering Garden Salad Regular"
    multiplier: float
    df: pd.DataFrame     # columns: ingredient, uom, type, exact_qty, pack_qty, qty_was_rounded


@dataclass
class PackingResult:
    df: pd.DataFrame                    # grand-total aggregated packing list
    sections: list[RecipeSection]       # per-dish breakdown (in invoice order)
    # invoice_item -> (matched BOM recipe name, effective multiplier)
    matches: dict[str, tuple[str, float] | None]
    unmatched: list[str]   # no BOM match and not skipped
    skipped: list[str]     # intentionally skipped by a rule
    warnings: list[str]    # human-readable warning strings


# ── Name normalisation ────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase and strip BOM suffix noise for name comparison."""
    text = text.lower().strip()
    text = re.sub(r"\s*-\s*mm$", "", text)      # " - MM"
    text = re.sub(r"\s+regular$", "", text)      # " Regular"
    text = re.sub(r"\s+dozen$", "", text)        # " Dozen"
    text = re.sub(r"\s+individual$", "", text)   # " Individual"
    text = re.sub(r"[-–]", " ", text)            # dashes → space
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _best_match(
    invoice_name: str,
    bom_recipes: list[str],
    prefer_variant: str | None = None,   # "individual" | "dozen" | None
) -> str | None:
    """Return the best-matching BOM recipe for an invoice item.

    prefer_variant biases the search toward Individual or Dozen variants
    when both exist in the BOM, giving cleaner (integer) multipliers.
    The formula self-corrects numerically either way, so this is a
    tie-breaker, not a hard requirement.
    """
    # 0. alias map — arbitrary names with no structural match
    alias = ALIAS_MAP.get(invoice_name.lower().strip())
    if alias and alias in bom_recipes:
        return alias

    candidates = [invoice_name]
    if not invoice_name.lower().startswith("catering"):
        candidates.append(f"Catering {invoice_name}")

    # Sort BOM list so the preferred variant comes first
    def _variant_rank(recipe: str) -> int:
        rl = recipe.lower()
        if prefer_variant == "individual" and "individual" in rl:
            return 0
        if prefer_variant == "dozen" and "dozen" in rl:
            return 0
        if prefer_variant and ("individual" in rl or "dozen" in rl):
            return 2   # wrong variant — deprioritize
        return 1

    ranked = sorted(bom_recipes, key=_variant_rank)

    for name in candidates:
        needle = _normalize(name)
        for r in ranked:
            if _normalize(r) == needle:
                return r
        for r in ranked:
            if _normalize(r).startswith(needle):
                return r
        for r in ranked:
            if needle.startswith(_normalize(r)):
                return r

    return None


# ── Multiplier computation ────────────────────────────────────────────────────

def _compute_multiplier(
    rule: ItemRule | None,
    invoice_qty: int,
    bom_unit_size: int,
) -> float:
    """Return the scaling factor to apply to BOM per-unit quantities.

    Rule types and their semantics
    ───────────────────────────────
    per_person (default)
        Invoice qty IS the multiplier (e.g. 82 guests, 4 salad bowls).
        No BOM unit-size correction.

    per_event
        Treat the whole order as a single event; multiplier = 1.

    individual_count
        Invoice qty is individual items. items_per_invoice_unit defaults to 1
        (or multiplier_override if set). Corrects for the BOM unit size so
        the total individual-item count is preserved:
            multiplier = (invoice_qty × items_per_invoice_unit) / bom_unit_size
        Example: 40 individual rolls, Dozen BOM (12/unit) → 40×1/12 ≈ 3.33
                 Applied to Dozen recipe: 3.33×12 = 40 rolls ✓

    dozen_count
        Invoice qty is dozens. items_per_invoice_unit defaults to 12 (or
        multiplier_override if set). Same formula as individual_count:
            multiplier = (invoice_qty × 12) / bom_unit_size
        Example: 2 dozen pies, Dozen BOM (12/unit) → 2×12/12 = 2 ✓
                 Applied to Dozen recipe: 2×12 = 24 pies ✓
        Self-corrects if matched to an Individual BOM (unit_size=1):
            2×12/1 = 24 → 24×1 = 24 pies ✓
    """
    if rule is None or rule.rule_type == "per_person":
        return float(rule.multiplier_override if rule and rule.multiplier_override is not None
                     else invoice_qty)

    if rule.rule_type == "per_event":
        return float(rule.multiplier_override if rule.multiplier_override is not None else 1)

    if rule.rule_type == "individual_count":
        items_per_inv = rule.multiplier_override if rule.multiplier_override is not None else 1
        return (invoice_qty * items_per_inv) / bom_unit_size

    if rule.rule_type == "dozen_count":
        items_per_inv = rule.multiplier_override if rule.multiplier_override is not None else 12
        return (invoice_qty * items_per_inv) / bom_unit_size

    # fallback
    return float(invoice_qty)


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_packing_list(
    invoice_items: list[dict],
    bom_df: pd.DataFrame,
    rules: list[ItemRule] | None = None,
) -> PackingResult:
    """
    For each invoice item:
      1. Apply any matching rule (skip / per_person / per_event /
         individual_count / dozen_count).
      2. Find the corresponding BOM recipe, biased toward the right variant.
      3. Compute the effective multiplier based on rule type and BOM unit size.
      4. Scale ingredient quantities and aggregate duplicates.
      5. Surface warnings for unusual quantities.
    """
    if rules is None:
        rules = load_rules()

    bom_recipes = bom_df["recipe"].unique().tolist()
    rows: list[pd.DataFrame] = []
    sections: list[RecipeSection] = []
    matches: dict[str, tuple[str, float] | None] = {}
    unmatched: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []

    for entry in invoice_items:
        name: str = entry["item"]
        invoice_qty: int = entry["quantity"]

        # ── 1. Apply config rule ──────────────────────────────────────────────
        rule = get_rule(name, rules)

        if rule and rule.rule_type == "skip":
            skipped.append(name)
            matches[name] = None
            continue

        # ── 2. Find BOM recipe, preferring the right variant ─────────────────
        prefer_variant: str | None = None
        if rule:
            if rule.rule_type == "individual_count":
                prefer_variant = "individual"
            elif rule.rule_type == "dozen_count":
                prefer_variant = "dozen"

        bom_match = _best_match(name, bom_recipes, prefer_variant)
        if bom_match is None:
            unmatched.append(name)
            matches[name] = None
            continue

        # ── 3. Compute effective multiplier ───────────────────────────────────
        bom_unit_size = infer_bom_unit_size(bom_match)
        multiplier = _compute_multiplier(rule, invoice_qty, bom_unit_size)
        matches[name] = (bom_match, multiplier)

        # Guest-count warning: only for items with NO explicit rule (ambiguous)
        if rule is None and invoice_qty > GUEST_COUNT_WARNING_THRESHOLD:
            warnings.append(
                f"GUEST COUNT?  '{name}' has invoice qty {invoice_qty} "
                f"(>{GUEST_COUNT_WARNING_THRESHOLD}). If this is a guest count "
                f"rather than a unit count, add a per_event rule in config/item_rules.csv."
            )

        # ── 4. Scale ingredients ──────────────────────────────────────────────
        recipe_rows = bom_df[bom_df["recipe"] == bom_match].copy()
        recipe_rows["scaled_net"]   = recipe_rows["net_qty"]   * multiplier
        recipe_rows["scaled_gross"] = recipe_rows["gross_qty"] * multiplier

        mult_label = f"{multiplier:.4g}" if multiplier != int(multiplier) else str(int(multiplier))
        recipe_rows["source"] = f"{name} ×{mult_label}"

        # Build per-section df: aggregate within the recipe (handles duplicate
        # ingredient rows), then apply rounding.
        sec_agg = (
            recipe_rows
            .groupby(["ingredient", "uom", "type"], as_index=False)
            .agg(total_gross_qty=("scaled_gross", "sum"))
            .sort_values(["type", "ingredient"])
            .reset_index(drop=True)
        )
        sec_agg = apply_rounding(sec_agg)
        sections.append(RecipeSection(
            invoice_item=name,
            bom_recipe=bom_match,
            multiplier=multiplier,
            df=sec_agg,
        ))

        rows.append(recipe_rows[["ingredient", "uom", "type",
                                  "scaled_net", "scaled_gross", "source"]])

    # ── Aggregate ─────────────────────────────────────────────────────────────
    empty_df = pd.DataFrame(columns=["ingredient", "uom", "type",
                                     "total_net_qty", "total_gross_qty",
                                     "exact_qty", "pack_qty", "qty_was_rounded",
                                     "source_recipes"])
    if not rows:
        return PackingResult(df=empty_df, sections=sections, matches=matches,
                             unmatched=unmatched, skipped=skipped,
                             warnings=warnings)

    combined = pd.concat(rows, ignore_index=True)
    agg = (
        combined
        .groupby(["ingredient", "uom", "type"], as_index=False)
        .agg(
            total_net_qty  =("scaled_net",   "sum"),
            total_gross_qty=("scaled_gross", "sum"),
            source_recipes =("source", lambda s: "; ".join(sorted(set(s)))),
        )
        .sort_values(["type", "ingredient"])
        .reset_index(drop=True)
    )

    # ── Rounding ──────────────────────────────────────────────────────────────
    agg = apply_rounding(agg)

    # ── Large-quantity warnings ───────────────────────────────────────────────
    for _, row in agg[agg["total_net_qty"] > LARGE_QTY_THRESHOLD].iterrows():
        warnings.append(
            f"LARGE QTY  '{row['ingredient']}' totals "
            f"{row['total_net_qty']:,.2f} {row['uom']} "
            f"(threshold: {LARGE_QTY_THRESHOLD}). "
            f"Source: {row['source_recipes']}"
        )

    return PackingResult(df=agg, sections=sections, matches=matches,
                         unmatched=unmatched, skipped=skipped,
                         warnings=warnings)
