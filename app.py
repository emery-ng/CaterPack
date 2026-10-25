import streamlit as st
import pandas as pd

from utils.invoice_parser import parse_invoice
from utils.bom_processor import load_bom
from utils.config_loader import load_rules
from utils.packing_generator import generate_packing_list
from utils.excel_export import export_to_excel

st.set_page_config(
    page_title="CaterPack",
    page_icon="🍽️",
    layout="wide",
)

# ── Session state ─────────────────────────────────────────────────────────────
# Persist results across reruns so tab-switching doesn't wipe the page.
for key in ("result", "excel_bytes", "invoice_name"):
    if key not in st.session_state:
        st.session_state[key] = None

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🍽️ CaterPack")
st.caption("Upload a Square invoice PDF and the BOM Excel file to generate a packing list.")

st.divider()

# ── File uploaders ────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    st.subheader("Step 1 · Square Invoice PDF")
    invoice_file = st.file_uploader(
        "Upload invoice PDF", type=["pdf"], key="invoice",
        label_visibility="collapsed",
    )
    if invoice_file:
        st.success(f"Loaded: **{invoice_file.name}**")

with col2:
    st.subheader("Step 2 · BOM Excel File")
    bom_file = st.file_uploader(
        "Upload BOM Excel", type=["xlsx", "xls"], key="bom",
        label_visibility="collapsed",
    )
    if bom_file:
        st.success(f"Loaded: **{bom_file.name}**")

st.divider()

# ── Generate button ───────────────────────────────────────────────────────────
ready = invoice_file and bom_file

if not ready:
    st.info("Upload both files above, then click Generate.")
    st.stop()

if st.button("Generate Packing List", type="primary", use_container_width=True):
    with st.spinner("Parsing invoice…"):
        invoice_items = parse_invoice(invoice_file.read())

    if not invoice_items:
        st.error("No line items found in the invoice — check that it is a Square-format PDF.")
        st.stop()

    with st.spinner("Loading BOM and rules…"):
        bom_df = load_bom(bom_file.read())
        rules  = load_rules()

    with st.spinner("Generating packing list…"):
        result      = generate_packing_list(invoice_items, bom_df, rules)
        excel_bytes = export_to_excel(result)

    st.session_state.result      = result
    st.session_state.excel_bytes = excel_bytes
    st.session_state.invoice_name = invoice_file.name
    st.session_state.invoice_items = invoice_items

if not st.session_state.result:
    st.stop()

# ── From here: show results ───────────────────────────────────────────────────
result      = st.session_state.result
excel_bytes = st.session_state.excel_bytes

# ── Summary bar ──────────────────────────────────────────────────────────────
n_dishes  = len(result.sections)
n_ingr    = len(result.df)
n_skip    = len(result.skipped)
n_unmatch = len(result.unmatched)
n_rounded = int(result.df["qty_was_rounded"].sum()) if "qty_was_rounded" in result.df.columns else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Dishes matched",   n_dishes)
m2.metric("Unique ingredients", n_ingr)
m3.metric("Skipped items",    n_skip)
m4.metric("Rounded up (EA)",  n_rounded)

st.divider()

# ── Warnings ──────────────────────────────────────────────────────────────────
if result.skipped:
    st.info(
        "**Skipped** (non-recipe line items — not included in packing list):\n"
        + "".join(f"\n- {s}" for s in result.skipped)
    )

if result.unmatched:
    st.warning(
        "**Unmatched items** — not found in BOM, excluded from packing list:\n"
        + "".join(f"\n- {u}" for u in result.unmatched)
        + "\n\n_To fix: add an alias in `ALIAS_MAP` or a rule in `config/item_rules.csv`._"
    )

guest_warnings = [w for w in result.warnings if w.startswith("GUEST COUNT")]
large_warnings  = [w for w in result.warnings if w.startswith("LARGE QTY")]

if guest_warnings:
    for w in guest_warnings:
        st.warning(f"⚠ {w}")

if large_warnings:
    with st.expander(f"⚠ {len(large_warnings)} ingredient(s) have unusually large quantities — click to review"):
        for w in large_warnings:
            st.warning(w)

# ── Invoice items (collapsed) ─────────────────────────────────────────────────
with st.expander("Detected invoice items", expanded=False):
    st.dataframe(
        pd.DataFrame(st.session_state.invoice_items)
          .rename(columns={"item": "Invoice Item", "quantity": "Qty"}),
        use_container_width=True,
        hide_index=True,
    )

st.divider()

# ── Packing list ──────────────────────────────────────────────────────────────
st.subheader("Packing List")

_SECTION_COLS = {
    "ingredient":      "Ingredient",
    "uom":             "UOM",
    "type":            "Type",
    "exact_qty":       "Exact Qty",
    "pack_qty":        "Pack Qty",
    "qty_was_rounded": "↑",
}
_TOTAL_COLS = {
    "ingredient":      "Ingredient",
    "uom":             "UOM",
    "type":            "Type",
    "exact_qty":       "Exact Qty",
    "pack_qty":        "Pack Qty",
    "qty_was_rounded": "↑",
    "source_recipes":  "Source (invoice items)",
}


def _format_df(df: pd.DataFrame, col_map: dict) -> pd.DataFrame:
    out = df.copy()
    if "qty_was_rounded" in out.columns:
        out["qty_was_rounded"] = out["qty_was_rounded"].map({True: "⬆", False: ""})
    cols = [c for c in col_map if c in out.columns]
    return out[cols].rename(columns=col_map)


_SVC_TYPE = "packaging / serviceware"

def _is_svc(t) -> bool:
    return str(t).strip().lower() == _SVC_TYPE


def _show_sections(sections, serviceware: bool) -> None:
    shown = 0
    for sec in sections:
        rows = sec.df[sec.df["type"].apply(_is_svc) == serviceware]
        if rows.empty:
            continue
        shown += 1
        mult_str = (
            f"{sec.multiplier:.4g}"
            if sec.multiplier != int(sec.multiplier)
            else str(int(sec.multiplier))
        )
        n_round_sec = int(rows["qty_was_rounded"].sum()) if "qty_was_rounded" in rows.columns else 0
        round_note  = f" · {n_round_sec} rounded ⬆" if n_round_sec else ""
        st.markdown(
            f"**{sec.invoice_item}** &nbsp;×{mult_str}&nbsp; "
            f"<span style='color:grey;font-size:0.85em'>→ {sec.bom_recipe} "
            f"· {len(rows)} items{round_note}</span>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            _format_df(rows, _SECTION_COLS),
            use_container_width=True,
            hide_index=True,
        )
    if shown == 0:
        st.info("Nothing to show.")


tab_food, tab_svc, tab_total = st.tabs(["Food", "Serviceware", "Grand Total"])

with tab_food:
    st.caption("Purchased ingredients and prepared sub-recipes — for kitchen staff.")
    _show_sections(result.sections, serviceware=False)

with tab_svc:
    st.caption("Packaging and serviceware — used by management for the utensils packing list.")
    _show_sections(result.sections, serviceware=True)

with tab_total:
    st.caption(
        f"{len(result.df)} unique ingredients across all dishes · "
        f"{n_rounded} rounded up to whole units"
    )
    st.dataframe(
        _format_df(result.df, _TOTAL_COLS),
        use_container_width=True,
        hide_index=True,
    )

# ── Download ──────────────────────────────────────────────────────────────────
st.divider()
fname = st.session_state.invoice_name.replace(".pdf", "_packing_list.xlsx")
st.download_button(
    label="Download packing_list.xlsx",
    data=excel_bytes,
    file_name=fname,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    use_container_width=True,
)
