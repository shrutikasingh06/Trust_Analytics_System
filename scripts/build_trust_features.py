"""
Run Track 1 trust feature builders, validate outputs, write feature_manifest.json.

Does not modify raw CSVs or existing behavior_eda/candidate_label parquets
except reading them. Writes only under dataset/cleaned/features/.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_product_features  # noqa: E402
import build_review_features  # noqa: E402
import build_reviewer_features  # noqa: E402
from features_common import (  # noqa: E402
    COMBINED,
    FEATURES_DIR,
    MANIFEST_OUT,
    PRODUCT_OUT,
    RAW_DIR,
    REVIEW_OUT,
    REVIEWER_OUT,
    REVIEWERS,
    cleanup_tmp,
    connect,
    parquet_rows,
    snapshot_mtimes,
    sql_path,
)


def _protected_paths() -> list[Path]:
    paths = [COMBINED, REVIEWERS]
    paths += sorted((COMBINED.parent / "reviews_by_category").glob("*.parquet"))
    paths += list(RAW_DIR.glob("*.csv"))
    cl = COMBINED.parents[1] / "candidate_label"
    paths += list(cl.glob("*.parquet"))
    return paths


def validate() -> dict:
    con = connect("4GB")
    report: dict = {}
    try:
        for name, path, key in [
            ("review", REVIEW_OUT, "review_id"),
            ("reviewer", REVIEWER_OUT, "reviewerID"),
            ("product", PRODUCT_OUT, "asin"),
        ]:
            src = sql_path(path)
            stats = con.execute(
                f"""
                SELECT
                    COUNT(*) AS n,
                    COUNT(DISTINCT {key}) AS n_distinct_key,
                    COUNT(*) - COUNT(DISTINCT {key}) AS duplicate_keys,
                    COUNT(*) FILTER (WHERE {key} IS NULL) AS null_key
                FROM read_parquet('{src}')
                """
            ).fetchdf().to_dict(orient="records")[0]
            cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()]
            inf_cols = {
                "review": ["rating", "helpful_ratio", "product_rating_deviation", "reviewer_rating_deviation"],
                "reviewer": ["avg_rating", "rating_variance", "reviews_per_active_day", "rating_entropy"],
                "product": ["avg_rating", "review_velocity", "rating_entropy", "avg_reviewer_rating_variability"],
            }[name]
            inf_expr = " + ".join(
                f'COUNT(*) FILTER (WHERE isnan("{c}") OR isinf("{c}"))' for c in inf_cols
            )
            inf_row = con.execute(
                f"SELECT {inf_expr} AS inf_nan_hits FROM read_parquet('{src}')"
            ).fetchone()
            inf_counts = {"selected_numeric_inf_or_nan_hits": int(inf_row[0] or 0)}
            null_rates = {"null_key": int(stats["null_key"])}
            extra = {}
            if name == "review":
                extra = con.execute(
                    f"""
                    SELECT
                        MIN(rating) AS min_rating,
                        MAX(rating) AS max_rating,
                        COUNT(*) FILTER (WHERE rating < 1 OR rating > 5) AS rating_oob,
                        COUNT(*) FILTER (WHERE review_length_chars < 0) AS neg_len,
                        COUNT(*) FILTER (WHERE exact_text_copy_count < 1) AS bad_dup_count,
                        COUNT(*) FILTER (WHERE review_hour <> 0) AS nonzero_hour
                    FROM read_parquet('{src}')
                    """
                ).fetchdf().to_dict(orient="records")[0]
            if name == "reviewer":
                extra = con.execute(
                    f"""
                    SELECT
                        COUNT(*) FILTER (WHERE review_span_days < 0) AS neg_span,
                        COUNT(*) FILTER (WHERE rating_std IS NULL) AS null_std,
                        COUNT(*) FILTER (WHERE rating_std IS NULL AND review_count = 1) AS null_std_n1,
                        COUNT(*) FILTER (WHERE rating_std IS NULL AND review_count > 1) AS null_std_n_gt1,
                        COUNT(*) FILTER (WHERE avg_rating < 1 OR avg_rating > 5) AS rating_oob,
                        MIN(review_count) AS min_n, MAX(review_count) AS max_n
                    FROM read_parquet('{src}')
                    """
                ).fetchdf().to_dict(orient="records")[0]
            if name == "product":
                extra = con.execute(
                    f"""
                    SELECT
                        COUNT(*) FILTER (WHERE review_span_days < 0) AS neg_span,
                        COUNT(*) FILTER (WHERE avg_rating < 1 OR avg_rating > 5) AS rating_oob,
                        COUNT(*) FILTER (WHERE rating_std IS NULL AND review_count = 1) AS null_std_n1,
                        COUNT(*) FILTER (WHERE rating_std IS NULL AND review_count > 1) AS null_std_n_gt1,
                        MIN(review_count) AS min_n, MAX(review_count) AS max_n
                    FROM read_parquet('{src}')
                    """
                ).fetchdf().to_dict(orient="records")[0]
            sample = con.execute(f"SELECT * FROM read_parquet('{src}') LIMIT 5").fetchdf()
            report[name] = {
                "path": str(path),
                "schema": cols,
                "n_features": len(cols),
                "row_stats": {k: (int(v) if isinstance(v, float) and v == int(v) else v) for k, v in stats.items()},
                "null_rates": null_rates,
                "inf_or_nan_by_column": inf_counts,
                "extra_checks": extra,
                "sample_head": sample.to_dict(orient="records"),
            }
            print(f"VALIDATE {name}: rows={stats['n']:,} distinct_{key}={stats['n_distinct_key']:,} extra={extra}", flush=True)
    finally:
        con.close()
        cleanup_tmp()
    return report


def mtime_report(before: dict[str, float], after: dict[str, float]) -> dict:
    out = {}
    for p, m0 in before.items():
        m1 = after.get(p)
        out[p] = {"unchanged": m1 is not None and abs(m1 - m0) < 0.5, "before": m0, "after": m1}
    return out


def main() -> None:
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    protected = _protected_paths()
    before = snapshot_mtimes(protected)
    t_all = time.time()

    r1 = build_review_features.build()
    r2 = build_reviewer_features.build()
    r3 = build_product_features.build()
    val = validate()
    after = snapshot_mtimes(protected)
    mt = mtime_report(before, after)
    changed = [p for p, x in mt.items() if not x["unchanged"]]
    if changed:
        print("WARNING: protected file mtimes changed:", changed, flush=True)
    else:
        print("Protected raw CSVs and existing parquets: mtimes unchanged.", flush=True)

    issues = []
    for level, block in val.items():
        rs = block["row_stats"]
        if rs.get("duplicate_keys"):
            issues.append(f"{level}: {rs['duplicate_keys']} duplicate keys")
        if rs.get("null_key"):
            issues.append(f"{level}: null keys")
        extra = block.get("extra_checks") or {}
        if extra.get("rating_oob"):
            issues.append(f"{level}: rating out of 1-5 ({extra['rating_oob']})")
        if extra.get("neg_span"):
            issues.append(f"{level}: negative span")
        if extra.get("null_std_n_gt1"):
            issues.append(f"{level}: rating_std NULL with n>1 ({extra['null_std_n_gt1']})")
        if extra.get("nonzero_hour"):
            issues.append(
                f"{level}: {extra['nonzero_hour']} rows with review_hour<>0 (timestamps may have time-of-day)"
            )
        inf_hits = (block.get("inf_or_nan_by_column") or {}).get("selected_numeric_inf_or_nan_hits")
        if inf_hits:
            issues.append(f"{level}: inf/nan hits on selected numeric columns = {inf_hits}")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "track": "trust_feature_engineering",
        "input_files": {
            "combined_reviews": str(COMBINED),
            "reviewers": str(REVIEWERS),
            "combined_rows": parquet_rows(COMBINED),
            "reviewer_rows_source": parquet_rows(REVIEWERS),
        },
        "output_files": {
            "review_features": str(REVIEW_OUT),
            "reviewer_features": str(REVIEWER_OUT),
            "product_features": str(PRODUCT_OUT),
        },
        "row_counts": {
            "review": parquet_rows(REVIEW_OUT),
            "reviewer": parquet_rows(REVIEWER_OUT),
            "product": parquet_rows(PRODUCT_OUT),
        },
        "feature_counts": {
            "review": val["review"]["n_features"],
            "reviewer": val["reviewer"]["n_features"],
            "product": val["product"]["n_features"],
        },
        "unavailable_fields": [
            "verified_purchase",
            "candidate_label on the 21.9M Track 1 corpus",
            "BFR on Track 1 corpus",
            "class as a trust label (rating polarity only)",
        ],
        "execution_time_sec": {
            "review": r1["execution_time_sec"],
            "reviewer": r2["execution_time_sec"],
            "product": r3["execution_time_sec"],
            "total": round(time.time() - t_all, 2),
        },
        "processing_method": {
            "review": r1["method"],
            "reviewer": r2["method"],
            "product": r3["method"],
        },
        "candidate_label": {
            "status": "heuristic candidate suspicious-review label; NOT verified fraud ground truth",
            "included_in_these_feature_tables": False,
            "location": "dataset/cleaned/candidate_label/",
        },
        "protected_mtime_unchanged": len(changed) == 0,
        "data_quality_issues": issues,
        "validation_summary": {
            level: {
                "schema": block["schema"],
                "row_stats": block["row_stats"],
                "extra_checks": block["extra_checks"],
            }
            for level, block in val.items()
        },
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"issues": issues, "times": manifest["execution_time_sec"], "rows": manifest["row_counts"]}, indent=2))
    print("Wrote", MANIFEST_OUT, flush=True)


if __name__ == "__main__":
    main()
