"""
Review-level trust/risk features from the Track 1 combined corpus.

Does not load the 21.9M-row parquet into pandas.
Does not regenerate existing behavior_eda parquet files.
Does not touch dataset/csv_files/.
Does not attach candidate_label or BFR (Track 2 only; leakage / different corpus).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features_common import (  # noqa: E402
    COMBINED,
    REVIEW_OUT,
    cleanup_tmp,
    connect,
    parquet_rows,
    sql_path,
)


def build(memory_limit: str = "4GB") -> dict:
    if not COMBINED.exists():
        raise FileNotFoundError(COMBINED)
    t0 = time.time()
    n_in = parquet_rows(COMBINED)
    src = sql_path(COMBINED)
    dst = sql_path(REVIEW_OUT)
    con = connect(memory_limit)
    try:
        if REVIEW_OUT.exists():
            REVIEW_OUT.unlink()
        con.execute(
            f"""
            COPY (
                WITH base AS (
                    SELECT
                        mongo_id AS review_id,
                        reviewerID,
                        asin,
                        category,
                        overall AS rating,
                        helpful_votes,
                        helpful_total,
                        helpful_ratio,
                        text_length AS review_length_chars,
                        CASE
                            WHEN text_missing = 1 OR reviewText IS NULL THEN 0
                            ELSE len(regexp_split_to_array(trim(CAST(reviewText AS VARCHAR)), '\\s+'))
                        END AS review_length_words,
                        CASE
                            WHEN summary_missing = 1 OR summary IS NULL THEN 0
                            ELSE length(CAST(summary AS VARCHAR))
                        END AS summary_length,
                        text_missing = 0 AS text_available,
                        summary_missing = 0 AS summary_available,
                        text_very_short,
                        unixReviewTime,
                        review_datetime_utc,
                        year(review_datetime_utc) AS review_year,
                        month(review_datetime_utc) AS review_month,
                        CAST(dayofweek(review_datetime_utc) AS INTEGER) AS review_day_of_week,
                        hour(review_datetime_utc) AS review_hour,
                        CASE
                            WHEN text_missing = 1 OR reviewText IS NULL THEN NULL
                            ELSE hash(CAST(reviewText AS VARCHAR))
                        END AS review_text_hash
                    FROM read_parquet('{src}')
                ),
                product_avg AS (
                    SELECT asin, AVG(rating) AS product_avg_rating
                    FROM base
                    GROUP BY asin
                ),
                reviewer_avg AS (
                    SELECT reviewerID, AVG(rating) AS reviewer_avg_rating
                    FROM base
                    GROUP BY reviewerID
                ),
                text_dup AS (
                    SELECT review_text_hash, COUNT(*) AS exact_text_copy_count
                    FROM base
                    WHERE review_text_hash IS NOT NULL
                    GROUP BY review_text_hash
                )
                SELECT
                    b.review_id,
                    b.reviewerID,
                    b.asin,
                    b.category,
                    b.rating,
                    b.helpful_votes,
                    b.helpful_total,
                    b.helpful_ratio,
                    b.review_length_chars,
                    b.review_length_words,
                    b.summary_length,
                    b.text_available,
                    b.summary_available,
                    b.text_very_short,
                    b.unixReviewTime,
                    b.review_datetime_utc,
                    b.review_year,
                    b.review_month,
                    b.review_day_of_week,
                    b.review_hour,
                    (b.rating - p.product_avg_rating) AS product_rating_deviation,
                    (b.rating - r.reviewer_avg_rating) AS reviewer_rating_deviation,
                    COALESCE(d.exact_text_copy_count, 1) AS exact_text_copy_count,
                    (COALESCE(d.exact_text_copy_count, 1) > 1) AS is_exact_duplicate_text
                FROM base b
                LEFT JOIN product_avg p ON b.asin = p.asin
                LEFT JOIN reviewer_avg r ON b.reviewerID = r.reviewerID
                LEFT JOIN text_dup d ON b.review_text_hash = d.review_text_hash
            )
            TO '{dst}' (FORMAT PARQUET, COMPRESSION SNAPPY)
            """
        )
        n_out = parquet_rows(REVIEW_OUT)
    finally:
        con.close()
        cleanup_tmp()
    elapsed = round(time.time() - t0, 2)
    print(f"review_features: {n_out:,} rows in {elapsed}s", flush=True)
    return {
        "output": str(REVIEW_OUT),
        "n_input_rows": n_in,
        "n_output_rows": n_out,
        "execution_time_sec": elapsed,
        "method": "DuckDB CTE + joins on parquet; COPY to parquet; no pandas load of combined file",
    }


if __name__ == "__main__":
    build()
