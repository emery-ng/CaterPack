import pandas as pd
from io import BytesIO


def load_bom(excel_bytes: bytes) -> pd.DataFrame:
    """
    Load the BOM Detail sheet from an Excel file.

    Returns a DataFrame with columns:
        recipe, ingredient, net_qty, gross_qty, uom, type
    """
    df = pd.read_excel(
        BytesIO(excel_bytes),
        sheet_name="BOM Detail",
        usecols=[1, 2, 3, 4, 5, 6],  # columns B-G
        header=0,
    )
    df.columns = ["recipe", "ingredient", "net_qty", "gross_qty", "uom", "type"]
    df = df.dropna(subset=["recipe", "ingredient"])
    df["net_qty"] = pd.to_numeric(df["net_qty"], errors="coerce").fillna(0)
    df["gross_qty"] = pd.to_numeric(df["gross_qty"], errors="coerce").fillna(0)
    return df.reset_index(drop=True)
