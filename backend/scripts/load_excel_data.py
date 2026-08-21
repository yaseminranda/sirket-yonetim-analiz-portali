"""Script that loads Excel/CSV files from the data folder into the database, cleaning and type-converting columns along the way."""
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import inspect, text, types as satypes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from database import engine

DATA_FOLDER = str(Path(__file__).resolve().parent.parent.parent / "dosyalar")
SCHEMA = "public"
IF_EXISTS = "append"

NULL_VALUES = {"null", "n/a", "na", "none", "-", "--", "nan", "yok", ""}
DATE_KEYWORDS = "tarih"
NUMERIC_KEYWORDS = ["maas", "kira", "fiyat", "ucret", "bedel", "metrekare", "depozito", "toplam", "total"]


def fix_turkish_uppercase(text_val):
    """Convert text to uppercase, correctly mapping Turkish-specific characters (ç, ğ, ı, ö, ş, ü)."""
    if not isinstance(text_val, str):
        text_val = str(text_val) if pd.notna(text_val) else text_val
        if not isinstance(text_val, str):
            return text_val
    conversion_map = {"ç": "C", "Ç": "C", "ğ": "G", "Ğ": "G", "ı": "I", "I": "I", "i": "I", "İ": "I", "ö": "O", "Ö": "O", "ş": "S", "Ş": "S", "ü": "U", "Ü": "U"}
    return "".join(conversion_map.get(k, k.upper()) for k in text_val)


def sanitize_sql_name(text_val):
    """Transliterate a string to ASCII and normalize it into a safe lowercase SQL identifier."""
    if not isinstance(text_val, str):
        text_val = str(text_val)
    conversion_map = {"ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i", "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u"}
    for k, v in conversion_map.items():
        text_val = text_val.replace(k, v)
    text_val = text_val.lower().strip()
    text_val = re.sub(r"[^\w]+", "_", text_val)
    return text_val.strip("_")


def clean_text_and_spaces(df):
    """Trim and collapse whitespace in every cell, convert null-like values to NaN, and uppercase remaining strings."""
    def clean_val(x):
        """Clean a single cell value, returning NaN for null-like content or normalized uppercase text."""
        if pd.isna(x):
            return np.nan
        if isinstance(x, str):
            cleaned = re.sub(r"\s+", " ", x.strip())
            if cleaned.lower() in NULL_VALUES or cleaned == "":
                return np.nan
            return fix_turkish_uppercase(cleaned)
        return x
    return df.map(clean_val)


def standardize_date(value):
    """Parse a raw date value, whether an Excel serial number or a delimited string, into an ISO 'YYYY-MM-DD' string."""
    if pd.isna(value):
        return np.nan
    str_val = str(value).strip()
    if str_val.lower() in NULL_VALUES or str_val == "":
        return np.nan
    try:
        if str_val.replace(".", "", 1).isdigit() and float(str_val) > 30000:
            return pd.to_datetime(float(str_val), unit="D", origin="1899-12-30").strftime("%Y-%m-%d")
    except Exception:
        pass
    normalized = re.sub(r"[.,\/_]", "-", str_val)
    parts = [p.strip() for p in normalized.split("-") if p.strip() != ""]
    if len(parts) == 3:
        if len(parts[0]) == 4:
            year, month, day = parts[0], parts[1], parts[2]
        elif len(parts[2]) == 4:
            day, month, year = parts[0], parts[1], parts[2]
        else:
            try:
                return pd.to_datetime(str_val, dayfirst=True).strftime("%Y-%m-%d")
            except Exception:
                return str_val
        try:
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        except Exception:
            return str_val
    try:
        return pd.to_datetime(str_val, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return str_val


def parse_numeric(value):
    """Parse a raw numeric value, handling Turkish thousands/decimal separators, into a float."""
    if pd.isna(value):
        return np.nan
    if isinstance(value, (int, float)):
        return value
    str_val = str(value).strip().replace(" ", "")
    if re.match(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$", str_val):
        str_val = str_val.replace(".", "").replace(",", ".")
    else:
        str_val = str_val.replace(",", ".")
    try:
        return float(str_val)
    except ValueError:
        return value


def detect_first_id_column(df):
    """Find the first column whose name suggests it is an identifier (contains 'id' or ends with 'no')."""
    for col in df.columns:
        col_lower = str(col).lower()
        if "id" in col_lower or col_lower.endswith("no") or "_no" in col_lower:
            return col
    return None


def get_table_name_from_file(raw_name):
    """Derive a sanitized database table name from a source file name, stripping any leading numeric prefix."""
    name, _ = os.path.splitext(raw_name)
    clean_name = re.sub(r"^\d+_", "", name)
    return sanitize_sql_name(clean_name)


def clean_dataframe(df):
    """Sanitize column names, clean text/date/numeric values, and drop empty rows/columns and duplicate rows."""
    df.columns = [sanitize_sql_name(c) for c in df.columns]
    df = clean_text_and_spaces(df)
    for col in df.columns:
        if DATE_KEYWORDS in col:
            df[col] = df[col].apply(standardize_date)
        if any(a in col for a in NUMERIC_KEYWORDS):
            df[col] = df[col].apply(parse_numeric)
    df = df.dropna(how="all", axis=1)
    df = df.dropna(how="all", axis=0)
    key_col = detect_first_id_column(df)
    if key_col is not None:
        df = df.dropna(subset=[key_col])
    df = df.drop_duplicates()
    return df.reset_index(drop=True)


def map_column_types_and_prepare(df):
    """Infer a SQLAlchemy column type (date, numeric, integer, or text) for each column and coerce the DataFrame accordingly."""
    dtype_map = {}
    for col in df.columns:
        if DATE_KEYWORDS in col:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
            dtype_map[col] = satypes.Date()
        elif any(a in col for a in NUMERIC_KEYWORDS):
            df[col] = pd.to_numeric(df[col], errors="coerce")
            dtype_map[col] = satypes.Numeric(12, 2)
        elif (col.endswith("_id") or col == "id") and "no" not in col:
            numeric_test = pd.to_numeric(df[col], errors="coerce")
            if numeric_test.notna().sum() == df[col].notna().sum():
                df[col] = numeric_test.astype("Int64")
                dtype_map[col] = satypes.Integer()
            else:
                dtype_map[col] = satypes.Text()
        else:
            dtype_map[col] = satypes.Text()
    return df, dtype_map


def write_to_database(df, table_name, dtype_map):
    """Write a DataFrame to the database, truncating the target table first if it already exists."""
    inspector = inspect(engine)
    table_exists = inspector.has_table(table_name, schema=SCHEMA)

    if table_exists:
        print(f" '{table_name}' tablosu mevcut. Veriler güncelleniyor (TRUNCATE)...")
        with engine.begin() as connection:
            connection.execute(text("SET session_replication_role = 'replica';"))
            connection.execute(text(f'TRUNCATE TABLE "{SCHEMA}"."{table_name}" CASCADE;'))
    else:
        print(f" '{table_name}' tablosu veritabanında bulunamadı. Yeni tablo oluşturuluyor...")

    df.to_sql(table_name, engine, schema=SCHEMA, if_exists=IF_EXISTS, index=False, dtype=dtype_map)

    if table_exists:
        with engine.begin() as connection:
            connection.execute(text("SET session_replication_role = 'origin';"))

    print(f"✅ '{table_name}' tablosuna {len(df)} satır veri başarıyla aktarıldı!\n")


def main():
    """Read every Excel/CSV file in the data folder, clean it, and load it into its corresponding database table."""
    if not os.path.exists(DATA_FOLDER):
        print(f"'{DATA_FOLDER}' klasörü bulunamadı!")
        return

    files = sorted(os.listdir(DATA_FOLDER))

    for file_name in files:
        if file_name.startswith("~$"):
            continue

        file_path = os.path.join(DATA_FOLDER, file_name)
        file_lower = file_name.lower()

        try:
            if file_lower.endswith(".csv"):
                df = pd.read_csv(file_path, dtype=str)
                df = clean_dataframe(df)
                df, dtype_map = map_column_types_and_prepare(df)
                table_name = get_table_name_from_file(file_name)
                write_to_database(df, table_name, dtype_map)

            elif file_lower.endswith((".xlsx", ".xls")):
                all_sheets = pd.read_excel(file_path, sheet_name=None, dtype=str)
                for sheet_name, df in all_sheets.items():
                    df = clean_dataframe(df)
                    df, dtype_map = map_column_types_and_prepare(df)
                    table_name = get_table_name_from_file(file_name if len(all_sheets) == 1 else sheet_name)
                    write_to_database(df, table_name, dtype_map)
        except Exception as e:
            print(f"Hata ({file_name}): {e}")


if __name__ == "__main__":
    main()
