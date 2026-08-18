"""Shared helpers for Trust Analytics cleaning. Never writes to dataset/csv_files/."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\Trust_Analytics_System")
RAW_DIR = ROOT / "dataset" / "csv_files"
CLEANED_DIR = ROOT / "dataset" / "cleaned"

CATEGORY_FILES = [
    "Cell_Phones_and_Accessories.csv",
    "Clothing_Shoes_and_Jewelry.csv",
    "Electronics.csv",
    "Home_and_Kitchen.csv",
    "Sports_and_Outdoors.csv",
    "Toys_and_Games.csv",
]

BFR_KEYS = ["CS", "MNR", "RB", "RC", "PR", "NR", "FR", "RSP", "AW", "RD", "RL", "ER", "PC"]
OID_RE = re.compile(r"\$oid['\"]?\s*:\s*['\"]([0-9a-fA-F]+)['\"]")
HELPFUL_RE = re.compile(r"^\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]$")


def miss_mask(s: pd.Series) -> pd.Series:
    t = s.fillna("").astype(str).str.strip()
    return t.eq("") | t.str.lower().isin(["nan", "none", "null"])


def extract_mongo_id(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.extract(OID_RE, expand=False)


def parse_helpful(s: pd.Series) -> pd.DataFrame:
    t = s.fillna("").astype(str).str.strip()
    extracted = t.str.extract(HELPFUL_RE, expand=True)
    votes = pd.to_numeric(extracted[0], errors="coerce")
    total = pd.to_numeric(extracted[1], errors="coerce")
    ratio = np.where((total.notna()) & (total > 0), votes / total, np.nan)
    return pd.DataFrame(
        {
            "helpful_votes": votes,
            "helpful_total": total,
            "helpful_ratio": ratio,
        }
    )


def parse_bfr_column(s: pd.Series) -> pd.DataFrame:
    """Vectorized-enough parse: regex per key, fallback NaN if missing."""
    text = s.fillna("").astype(str)
    out = {}
    for key in BFR_KEYS:
        # Require quoted key so NR does not match inside MNR.
        pat = rf"['\"]{key}['\"]\s*:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
        out[f"bfr_{key}"] = pd.to_numeric(text.str.extract(pat, expand=False), errors="coerce")
    return pd.DataFrame(out)


def clean_review_frame(chunk: pd.DataFrame, source_file: str) -> pd.DataFrame:
    """Normalize a raw Amazon-style review chunk. Does not drop rows."""
    df = pd.DataFrame(index=chunk.index)
    df["source_file"] = source_file
    if "_id" in chunk.columns:
        df["mongo_id"] = extract_mongo_id(chunk["_id"])
    else:
        df["mongo_id"] = pd.NA

    df["reviewerID"] = chunk["reviewerID"].astype(str)
    df["asin"] = chunk["asin"].astype(str)

    if "reviewerName" in chunk.columns:
        name = chunk["reviewerName"].astype(str)
        df["reviewerName"] = name.where(~miss_mask(chunk["reviewerName"]), pd.NA)
    else:
        df["reviewerName"] = pd.NA

    helpful = parse_helpful(chunk["helpful"])
    df["helpful_votes"] = helpful["helpful_votes"].to_numpy()
    df["helpful_total"] = helpful["helpful_total"].to_numpy()
    df["helpful_ratio"] = helpful["helpful_ratio"]

    text_missing = miss_mask(chunk["reviewText"])
    text = chunk["reviewText"].astype(str)
    df["reviewText"] = text.where(~text_missing, pd.NA)
    df["text_length"] = np.where(text_missing, 0, text.str.len())
    df["text_missing"] = text_missing.astype("int8")
    df["text_very_short"] = ((~text_missing) & (text.str.len() < 5)).astype("int8")

    if "summary" in chunk.columns:
        sum_missing = miss_mask(chunk["summary"])
        df["summary"] = chunk["summary"].astype(str).where(~sum_missing, pd.NA)
        df["summary_missing"] = sum_missing.astype("int8")
    else:
        df["summary"] = pd.NA
        df["summary_missing"] = np.int8(1)

    overall = pd.to_numeric(chunk["overall"], errors="coerce")
    df["overall"] = overall
    df["rating_polarity"] = np.where(overall >= 4, 1, np.where(overall.notna(), 0, np.nan))

    unix = pd.to_numeric(chunk["unixReviewTime"], errors="coerce")
    df["unixReviewTime"] = unix
    df["review_datetime_utc"] = pd.to_datetime(unix, unit="s", utc=True)

    df["category"] = chunk["category"].astype(str) if "category" in chunk.columns else pd.NA

    if "class" in chunk.columns:
        cls_missing = miss_mask(chunk["class"])
        cls = pd.to_numeric(chunk["class"], errors="coerce")
        df["class_raw"] = cls.where(~cls_missing, np.nan)
        df["class_missing"] = cls_missing.astype("int8")
    else:
        df["class_raw"] = np.nan
        df["class_missing"] = np.int8(1)

    df["row_key_hash"] = pd.util.hash_pandas_object(
        pd.DataFrame(
            {
                "r": df["reviewerID"],
                "a": df["asin"],
                "u": df["unixReviewTime"].astype("Int64").astype(str),
            }
        ),
        index=False,
    )
    return df


REVIEW_SCHEMA_DTYPES = {
    "source_file": "string",
    "mongo_id": "string",
    "reviewerID": "string",
    "asin": "string",
    "reviewerName": "string",
    "helpful_votes": "float64",
    "helpful_total": "float64",
    "helpful_ratio": "float64",
    "reviewText": "string",
    "text_length": "int32",
    "text_missing": "int8",
    "text_very_short": "int8",
    "summary": "string",
    "summary_missing": "int8",
    "overall": "float64",
    "rating_polarity": "float64",
    "unixReviewTime": "float64",
    "review_datetime_utc": "datetime64[ns, UTC]",
    "category": "string",
    "class_raw": "float64",
    "class_missing": "int8",
    "row_key_hash": "uint64",
}


def apply_review_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    for col, dtype in REVIEW_SCHEMA_DTYPES.items():
        if col not in df.columns:
            continue
        if dtype == "datetime64[ns, UTC]":
            continue
        try:
            df[col] = df[col].astype(dtype)
        except Exception:
            pass
    return df
