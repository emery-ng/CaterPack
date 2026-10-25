import csv
from dataclasses import dataclass
from pathlib import Path

RULES_FILE = Path(__file__).parent.parent / "config" / "item_rules.csv"

# Warn when any single aggregated ingredient exceeds this total quantity.
LARGE_QTY_THRESHOLD = 1000

# Warn when a per_person item is ordered in quantities that likely represent
# a guest count rather than a unit count (e.g. 82 Sandwich Platters).
GUEST_COUNT_WARNING_THRESHOLD = 50


@dataclass(frozen=True)
class ItemRule:
    pattern: str
    match_type: str          # exact | startswith | contains
    rule_type: str           # skip | per_person | per_event | individual_count | dozen_count
    multiplier_override: float | None
    notes: str


def infer_bom_unit_size(bom_recipe_name: str) -> int:
    """Return how many individual items one unit of this BOM recipe represents.

    Inferred from the recipe name: "Dozen" → 12, otherwise 1.
    """
    return 12 if "dozen" in bom_recipe_name.lower() else 1


def load_rules(path: Path = RULES_FILE) -> list[ItemRule]:
    rules: list[ItemRule] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mo = row["multiplier_override"].strip()
            rules.append(ItemRule(
                pattern=row["pattern"].strip(),
                match_type=row["match_type"].strip().lower(),
                rule_type=row["rule_type"].strip().lower(),
                multiplier_override=float(mo) if mo else None,
                notes=row["notes"].strip(),
            ))
    return rules


def get_rule(invoice_name: str, rules: list[ItemRule]) -> ItemRule | None:
    """Return the first rule whose pattern matches invoice_name, or None."""
    name_lower = invoice_name.lower().strip()
    for rule in rules:
        pat = rule.pattern.lower()
        match rule.match_type:
            case "exact"      if name_lower == pat:           return rule
            case "startswith" if name_lower.startswith(pat):  return rule
            case "contains"   if pat in name_lower:           return rule
    return None
