"""Shared paths and DuckDB helpers for trust feature engineering."""
from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

ROOT = Path(r"D:\Trust_Analytics_System")
COMBINED = ROOT / "dataset" / "cleaned" / "behavior_eda" / "reviews_combined_dedup.parquet"
REVIEWERS = ROOT / "dataset" / "cleaned" / "behavior_eda" / "reviewers.parquet"
CANDIDATE_ANALYSIS = ROOT / "dataset" / "cleaned" / "candidate_label" / "reviews_analysis.parquet"
FEATURES_DIR = ROOT / "dataset" / "cleaned" / "features"
TMP_DIR = FEATURES_DIR / "_duckdb_tmp"

REVIEW_OUT = FEATURES_DIR / "review_features.parquet"
REVIEWER_OUT = FEATURES_DIR / "reviewer_features.parquet"
PRODUCT_OUT = FEATURES_DIR / "product_features.parquet"
MANIFEST_OUT = FEATURES_DIR / "feature_manifest.json"

RAW_DIR = ROOT / "dataset" / "csv_files"


def sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def connect(memory_limit: str = "4GB") -> duckdb.DuckDBPyConnection:
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET temp_directory='{sql_path(TMP_DIR)}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def cleanup_tmp() -> None:
    shutil.rmtree(TMP_DIR, ignore_errors=True)


def parquet_rows(path: Path) -> int:
    return int(pq.ParquetFile(path).metadata.num_rows)


def snapshot_mtimes(paths: list[Path]) -> dict[str, float]:
    return {str(p): p.stat().st_mtime for p in paths if p.exists()}
