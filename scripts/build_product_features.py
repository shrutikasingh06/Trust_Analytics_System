"""
Product-level behavioral features from the Track 1 combined corpus.

Joins reviewer-level stats without loading all reviews into pandas.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features_common import (  # noqa: E402
    COMBINED,
    PRODUCT_OUT,
    REVIEWERS,
    cleanup_tmp,
    connect,
    parquet_rows,
    sql_path,
)


def build(memory_limit: str = "4GB") -> dict:
    if not COMBINED.exists() or not REVIEWERS.exists():
        raise FileNotFoundError("Need combined reviews and reviewers parquet")
    t0 = time.time()
    src = sql_path(COMBINED)
    src_rev = sql_path(REVIEWERS)
    dst = sql_path(PRODUCT_OUT)
    con = connect(memory_limit)
    try:
        if PRODUCT_OUT.exists():
            PRODUCT_OUT.unlink()
        con.execute(
            f"""
            COPY (
                WITH reviews AS (
                    SELECT
                        asin,
                        reviewerID,
                        category,
                        overall,
                        helpful_votes,
                        text_missing,
                        unixReviewTime
                    FROM read_parquet('{src}')
                ),
                joined AS (
                    SELECT
                        r.*,
                        rv.review_count AS reviewer_review_count,
                        rv.unique_products AS reviewer_unique_products,
                        rv.rating_std AS reviewer_rating_std
                    FROM reviews r
                    LEFT JOIN read_parquet('{src_rev}') rv
                      ON r.reviewerID = rv.reviewerID
                )
                SELECT
                    asin,
                    COUNT(*) AS review_count,
                    COUNT(DISTINCT reviewerID) AS unique_reviewers,
                    COUNT(DISTINCT category) AS n_categories,
                    MIN(category) AS category,
                    AVG(overall) AS avg_rating,
                    STDDEV_SAMP(overall) AS rating_std,
                    MIN(overall) AS rating_min,
                    MAX(overall) AS rating_max,
                    (MAX(overall) - MIN(overall)) AS rating_range,
                    SUM(CASE WHEN overall = 5 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS five_star_ratio,
                    SUM(CASE WHEN overall = 4 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS four_star_ratio,
                    SUM(CASE WHEN overall = 3 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS three_star_ratio,
                    SUM(CASE WHEN overall = 2 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS two_star_ratio,
                    SUM(CASE WHEN overall = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS one_star_ratio,
                    (
                        - (
                            CASE WHEN SUM(CASE WHEN overall = 1 THEN 1 ELSE 0 END) > 0
                                 THEN (SUM(CASE WHEN overall = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                      * ln(SUM(CASE WHEN overall = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                 ELSE 0 END
                          + CASE WHEN SUM(CASE WHEN overall = 2 THEN 1 ELSE 0 END) > 0
                                 THEN (SUM(CASE WHEN overall = 2 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                      * ln(SUM(CASE WHEN overall = 2 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                 ELSE 0 END
                          + CASE WHEN SUM(CASE WHEN overall = 3 THEN 1 ELSE 0 END) > 0
                                 THEN (SUM(CASE WHEN overall = 3 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                      * ln(SUM(CASE WHEN overall = 3 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                 ELSE 0 END
                          + CASE WHEN SUM(CASE WHEN overall = 4 THEN 1 ELSE 0 END) > 0
                                 THEN (SUM(CASE WHEN overall = 4 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                      * ln(SUM(CASE WHEN overall = 4 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                 ELSE 0 END
                          + CASE WHEN SUM(CASE WHEN overall = 5 THEN 1 ELSE 0 END) > 0
                                 THEN (SUM(CASE WHEN overall = 5 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                      * ln(SUM(CASE WHEN overall = 5 THEN 1 ELSE 0 END) * 1.0 / COUNT(*))
                                 ELSE 0 END
                        )
                    ) AS rating_entropy,
                    AVG(helpful_votes) AS avg_helpful_votes,
                    AVG(CASE WHEN text_missing = 0 THEN 1.0 ELSE 0.0 END) AS review_text_coverage,
                    MIN(unixReviewTime) AS first_review_unix,
                    MAX(unixReviewTime) AS last_review_unix,
                    CASE
                        WHEN MIN(unixReviewTime) IS NULL OR MAX(unixReviewTime) IS NULL THEN NULL
                        ELSE (MAX(unixReviewTime) - MIN(unixReviewTime)) / 86400.0
                    END AS review_span_days,
                    COUNT(*) * 1.0 / ((MAX(unixReviewTime) - MIN(unixReviewTime)) / 86400.0 + 1.0) AS review_velocity,
                    AVG(reviewer_review_count) AS avg_reviewer_review_count,
                    AVG(reviewer_unique_products) AS avg_reviewer_product_diversity,
                    AVG(reviewer_rating_std) AS avg_reviewer_rating_variability
                FROM joined
                GROUP BY asin
            )
            TO '{dst}' (FORMAT PARQUET, COMPRESSION SNAPPY)
            """
        )
        n_out = parquet_rows(PRODUCT_OUT)
    finally:
        con.close()
        cleanup_tmp()
    elapsed = round(time.time() - t0, 2)
    print(f"product_features: {n_out:,} rows in {elapsed}s", flush=True)
    return {
        "output": str(PRODUCT_OUT),
        "n_output_rows": n_out,
        "execution_time_sec": elapsed,
        "method": "DuckDB GROUP BY asin on combined parquet, LEFT JOIN reviewers.parquet aggregates",
    }


if __name__ == "__main__":
    build()
