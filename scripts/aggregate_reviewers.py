"""
Memory-safe reviewer aggregation for Track 1.

Reads existing reviews_combined_dedup.parquet via DuckDB (column projection,
disk spill). Does not regenerate category or combined parquet files.
Does not touch dataset/csv_files/.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

ROOT = Path(r"D:\Trust_Analytics_System")
COMBINED = ROOT / "dataset" / "cleaned" / "behavior_eda" / "reviews_combined_dedup.parquet"
OUT_PARQUET = ROOT / "dataset" / "cleaned" / "behavior_eda" / "reviewers.parquet"
OUT_MANIFEST = ROOT / "dataset" / "cleaned" / "behavior_eda" / "manifest.json"
TMP_DIR = ROOT / "dataset" / "cleaned" / "behavior_eda" / "_duckdb_tmp"

# Columns that exist on the combined parquet and are used here.
USED_COLUMNS = [
    "reviewerID",
    "asin",
    "category",
    "overall",
    "text_length",
    "text_missing",
    "helpful_votes",
    "unixReviewTime",
    "review_datetime_utc",
]

UNAVAILABLE = [
    "verified_purchase_count — no verified-purchase column exists on the source parquet",
]


def _sql(src: str, dst: str) -> str:
    return f"""
COPY (
    SELECT
        reviewerID,
        COUNT(*) AS review_count,
        COUNT(DISTINCT asin) AS unique_products,
        COUNT(DISTINCT category) AS unique_categories,
        AVG(overall) AS avg_rating,
        STDDEV_SAMP(overall) AS rating_std,
        MIN(overall) AS rating_min,
        MAX(overall) AS rating_max,
        (MAX(overall) - MIN(overall)) AS rating_range,
        SUM(CASE WHEN text_missing = 0 THEN 1 ELSE 0 END) AS review_text_count,
        AVG(text_length) AS avg_review_length,
        MEDIAN(text_length) AS median_review_length,
        SUM(helpful_votes) AS helpful_votes_total,
        MIN(unixReviewTime) AS first_review_unix,
        MAX(unixReviewTime) AS last_review_unix,
        MIN(review_datetime_utc) AS first_review_time,
        MAX(review_datetime_utc) AS last_review_time,
        CASE
            WHEN MIN(unixReviewTime) IS NULL OR MAX(unixReviewTime) IS NULL THEN NULL
            ELSE (MAX(unixReviewTime) - MIN(unixReviewTime)) / 86400.0
        END AS review_span_days
    FROM read_parquet('{src}')
    GROUP BY reviewerID
)
TO '{dst}' (FORMAT PARQUET, COMPRESSION SNAPPY)
"""


def aggregate(memory_limit: str = "3GB") -> dict:
    if not COMBINED.exists():
        raise FileNotFoundError(f"Combined parquet not found: {COMBINED}")

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    con = duckdb.connect(database=":memory:")
    try:
        con.execute(f"SET memory_limit='{memory_limit}'")
        con.execute(f"SET temp_directory='{TMP_DIR.as_posix()}'")
        con.execute("SET preserve_insertion_order=false")
        n_in = int(pq.ParquetFile(COMBINED).metadata.num_rows)
        print(f"Input rows (parquet footer): {n_in:,}", flush=True)
        if OUT_PARQUET.exists():
            OUT_PARQUET.unlink()
        src = COMBINED.as_posix().replace("'", "''")
        dst = OUT_PARQUET.as_posix().replace("'", "''")
        print("Running GROUP BY reviewerID (spilling to disk if needed)...", flush=True)
        con.execute(_sql(src, dst))
        n_reviewers = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{dst}')"
        ).fetchone()[0]
        cols = [
            r[0]
            for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{dst}')").fetchall()
        ]
        nulls = con.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE reviewerID IS NULL) AS null_reviewerID,
                COUNT(*) FILTER (WHERE review_count IS NULL OR review_count < 1) AS bad_review_count,
                COUNT(*) FILTER (WHERE unique_products IS NULL) AS null_unique_products,
                COUNT(*) FILTER (WHERE avg_rating IS NULL) AS null_avg_rating,
                COUNT(*) FILTER (WHERE rating_std IS NULL) AS null_rating_std,
                COUNT(*) FILTER (WHERE review_span_days < 0) AS negative_span
            FROM read_parquet('{dst}')
            """
        ).fetchdf().to_dict(orient="records")[0]
    finally:
        con.close()
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    elapsed = round(time.time() - t0, 2)
    manifest = {
        "track": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_parquet": str(COMBINED),
        "output_parquet": str(OUT_PARQUET),
        "n_input_rows": int(n_in),
        "n_reviewer_rows": int(n_reviewers),
        "columns_produced": cols,
        "source_columns_used": USED_COLUMNS,
        "unavailable_columns": UNAVAILABLE,
        "execution_time_sec": elapsed,
        "method": (
            "DuckDB read_parquet + GROUP BY reviewerID with memory_limit="
            f"{memory_limit} and temp_directory spill; COPY to parquet. "
            "Did not load the combined file into pandas. "
            "Did not regenerate category or combined parquet files."
        ),
        "notes": [
            "rating_std is sample standard deviation (STDDEV_SAMP); NULL when a reviewer has a single review.",
            "review_text_count counts rows with text_missing = 0.",
            "verified_purchase_count was not created because the source has no such field.",
            "Existing reviews_by_category/*.parquet and reviews_combined_dedup.parquet were left in place.",
        ],
        "null_and_validity_checks": {k: int(v) if v is not None else None for k, v in nulls.items()},
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {n_reviewers:,} reviewers in {elapsed}s -> {OUT_PARQUET}", flush=True)
    return manifest


if __name__ == "__main__":
    aggregate()
