"""
Reviewer-level behavioral features.

Starts from behavior_eda/reviewers.parquet and joins additional
rating-distribution stats computed from the combined review parquet via DuckDB.
Does not load 21.9M rows into pandas.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features_common import (  # noqa: E402
    COMBINED,
    REVIEWER_OUT,
    REVIEWERS,
    cleanup_tmp,
    connect,
    parquet_rows,
    sql_path,
)


def build(memory_limit: str = "4GB") -> dict:
    if not REVIEWERS.exists() or not COMBINED.exists():
        raise FileNotFoundError("Need reviewers.parquet and combined reviews parquet")
    t0 = time.time()
    src_rev = sql_path(REVIEWERS)
    src_reviews = sql_path(COMBINED)
    dst = sql_path(REVIEWER_OUT)
    con = connect(memory_limit)
    try:
        if REVIEWER_OUT.exists():
            REVIEWER_OUT.unlink()
        con.execute(
            f"""
            COPY (
                WITH dist AS (
                    SELECT
                        reviewerID,
                        SUM(CASE WHEN overall = 1 THEN 1 ELSE 0 END) AS n_rating_1,
                        SUM(CASE WHEN overall = 2 THEN 1 ELSE 0 END) AS n_rating_2,
                        SUM(CASE WHEN overall = 3 THEN 1 ELSE 0 END) AS n_rating_3,
                        SUM(CASE WHEN overall = 4 THEN 1 ELSE 0 END) AS n_rating_4,
                        SUM(CASE WHEN overall = 5 THEN 1 ELSE 0 END) AS n_rating_5
                    FROM read_parquet('{src_reviews}')
                    GROUP BY reviewerID
                )
                SELECT
                    rv.reviewerID,
                    rv.review_count,
                    rv.unique_products,
                    rv.unique_categories,
                    rv.avg_rating,
                    rv.rating_std,
                    (rv.rating_std * rv.rating_std) AS rating_variance,
                    rv.rating_min,
                    rv.rating_max,
                    rv.rating_range,
                    rv.review_text_count,
                    rv.avg_review_length,
                    rv.median_review_length,
                    rv.helpful_votes_total,
                    (rv.helpful_votes_total / rv.review_count) AS average_helpfulness_per_review,
                    rv.first_review_time,
                    rv.last_review_time,
                    rv.first_review_unix,
                    rv.last_review_unix,
                    rv.review_span_days,
                    (rv.review_span_days + 1.0) AS inclusive_active_days,
                    (rv.review_count / (rv.review_span_days + 1.0)) AS reviews_per_active_day,
                    (rv.unique_products * 1.0 / rv.unique_categories) AS products_per_category,
                    d.n_rating_1,
                    d.n_rating_2,
                    d.n_rating_3,
                    d.n_rating_4,
                    d.n_rating_5,
                    (d.n_rating_5 * 1.0 / rv.review_count) AS five_star_ratio,
                    (d.n_rating_1 * 1.0 / rv.review_count) AS one_star_ratio,
                    ((d.n_rating_1 + d.n_rating_5) * 1.0 / rv.review_count) AS extreme_rating_ratio,
                    (
                        - (
                            CASE WHEN d.n_rating_1 > 0 THEN (d.n_rating_1 * 1.0 / rv.review_count) * ln(d.n_rating_1 * 1.0 / rv.review_count) ELSE 0 END
                          + CASE WHEN d.n_rating_2 > 0 THEN (d.n_rating_2 * 1.0 / rv.review_count) * ln(d.n_rating_2 * 1.0 / rv.review_count) ELSE 0 END
                          + CASE WHEN d.n_rating_3 > 0 THEN (d.n_rating_3 * 1.0 / rv.review_count) * ln(d.n_rating_3 * 1.0 / rv.review_count) ELSE 0 END
                          + CASE WHEN d.n_rating_4 > 0 THEN (d.n_rating_4 * 1.0 / rv.review_count) * ln(d.n_rating_4 * 1.0 / rv.review_count) ELSE 0 END
                          + CASE WHEN d.n_rating_5 > 0 THEN (d.n_rating_5 * 1.0 / rv.review_count) * ln(d.n_rating_5 * 1.0 / rv.review_count) ELSE 0 END
                        )
                    ) AS rating_entropy
                FROM read_parquet('{src_rev}') rv
                LEFT JOIN dist d ON rv.reviewerID = d.reviewerID
            )
            TO '{dst}' (FORMAT PARQUET, COMPRESSION SNAPPY)
            """
        )
        n_out = parquet_rows(REVIEWER_OUT)
    finally:
        con.close()
        cleanup_tmp()
    elapsed = round(time.time() - t0, 2)
    print(f"reviewer_features: {n_out:,} rows in {elapsed}s", flush=True)
    return {
        "output": str(REVIEWER_OUT),
        "n_input_reviewers": parquet_rows(REVIEWERS),
        "n_output_rows": n_out,
        "execution_time_sec": elapsed,
        "method": "DuckDB: start from reviewers.parquet, join rating histogram from combined parquet GROUP BY",
    }


if __name__ == "__main__":
    build()
