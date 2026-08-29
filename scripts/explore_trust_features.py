"""
Exploratory analysis of Track 1 trust feature tables.

Memory-safe: DuckDB SQL aggregations on parquet. Does not load the 21.9M-row
review table into pandas. Does not modify source feature parquets or raw CSVs.
Does not train models. review_hour is excluded (not genuine time-of-day).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features_common import (  # noqa: E402
    CANDIDATE_ANALYSIS,
    COMBINED,
    FEATURES_DIR,
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

ROOT = Path(r"D:\Trust_Analytics_System")
FIG_DIR = ROOT / "reports" / "eda" / "trust_features"
REPORT_MD = ROOT / "reports" / "eda" / "TRUST_FEATURE_EDA_REPORT.md"
EDA_JSON = FEATURES_DIR / "eda_summary.json"
TMP = FEATURES_DIR / "_duckdb_tmp_eda"

REVIEW_NUM = [
    "rating",
    "review_length_chars",
    "review_length_words",
    "summary_length",
    "helpful_votes",
    "helpful_total",
    "helpful_ratio",
    "product_rating_deviation",
    "reviewer_rating_deviation",
    "exact_text_copy_count",
    "review_year",
    "review_month",
    "review_day_of_week",
]
REVIEWER_NUM = [
    "review_count",
    "unique_products",
    "unique_categories",
    "avg_rating",
    "rating_std",
    "rating_range",
    "review_text_count",
    "avg_review_length",
    "median_review_length",
    "helpful_votes_total",
    "review_span_days",
    "reviews_per_active_day",
    "products_per_category",
    "rating_entropy",
    "extreme_rating_ratio",
    "five_star_ratio",
    "one_star_ratio",
    "rating_variance",
    "average_helpfulness_per_review",
]
PRODUCT_NUM = [
    "review_count",
    "unique_reviewers",
    "avg_rating",
    "rating_std",
    "rating_range",
    "five_star_ratio",
    "four_star_ratio",
    "three_star_ratio",
    "two_star_ratio",
    "one_star_ratio",
    "rating_entropy",
    "avg_helpful_votes",
    "review_text_coverage",
    "review_velocity",
    "avg_reviewer_review_count",
    "avg_reviewer_product_diversity",
    "avg_reviewer_rating_variability",
]
SKEW_PCT = [0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99]


def _q(con, sql):
    return con.execute(sql).fetchdf()


def describe_cols(con, src: str, cols: list[str], batch_size: int = 3) -> dict:
    """Quantiles are memory-heavy; describe a few columns per scan."""
    out: dict = {}
    for i in range(0, len(cols), batch_size):
        batch = cols[i : i + batch_size]
        parts = []
        for col in batch:
            parts.append(f"COUNT({col}) AS {col}__n_non_null")
            parts.append(f"COUNT(*) - COUNT({col}) AS {col}__n_null")
            parts.append(f"AVG({col}) AS {col}__mean")
            parts.append(f"MEDIAN({col}) AS {col}__median")
            parts.append(f"STDDEV_SAMP({col}) AS {col}__std")
            parts.append(f"MIN({col}) AS {col}__min")
            parts.append(f"MAX({col}) AS {col}__max")
            for p in SKEW_PCT:
                if p == 0.50:
                    continue
                parts.append(f"approx_quantile({col}, {p}) AS {col}__p{int(p*100):02d}")
        row = con.execute(f"SELECT {', '.join(parts)} FROM read_parquet('{src}')").fetchdf().iloc[0]
        for col in batch:
            d = {}
            for suffix in ["n_non_null", "n_null", "mean", "median", "std", "min", "max"]:
                v = row[f"{col}__{suffix}"]
                d[suffix] = _num(v, int_ok=suffix.startswith("n_"))
            for p in SKEW_PCT:
                if p == 0.50:
                    d["p50"] = d["median"]
                else:
                    d[f"p{int(p*100):02d}"] = _num(row[f"{col}__p{int(p*100):02d}"])
            out[col] = d
        print(f"  described {batch} from {src}", flush=True)
    return out


def _num(v, int_ok: bool = False):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    if int_ok:
        return int(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return float(v)


def pearson_matrix(con, src: str, cols: list[str]) -> dict:
    parts = []
    names = []
    # Compute pairwise corr in row-chunks of columns to limit memory.
    mat = {c: {d: None for d in cols} for c in cols}
    high = []
    for i, a in enumerate(cols):
        parts = [f"corr({a}, {b}) AS \"{b}\"" for b in cols[i:]]
        row = con.execute(f"SELECT {', '.join(parts)} FROM read_parquet('{src}')").fetchdf().iloc[0]
        for b, val in zip(cols[i:], row.tolist()):
            v = None if val is None or (isinstance(val, float) and np.isnan(val)) else float(val)
            mat[a][b] = v
            mat[b][a] = v
            if a != b and v is not None and abs(v) >= 0.80:
                high.append({"a": a, "b": b, "pearson": v})
        print(f"  pearson row {a}", flush=True)
    high.sort(key=lambda x: -abs(x["pearson"]))
    return {"matrix": mat, "high_pairs_abs_ge_0_80": high}


def spearman_sample(con, src: str, cols: list[str], n: int = 250_000) -> dict:
    """Spearman on a random sample (documented). Ranks computed after sampling."""
    col_sql = ", ".join(cols)
    df = con.execute(
        f"SELECT {col_sql} FROM read_parquet('{src}') USING SAMPLE {n}"
    ).fetchdf()
    corr = df.corr(method="spearman", numeric_only=True)
    high = []
    for i, a in enumerate(corr.columns):
        for b in corr.columns[i + 1 :]:
            v = corr.loc[a, b]
            if pd.notna(v) and abs(float(v)) >= 0.80:
                high.append({"a": a, "b": b, "spearman": float(v)})
    high.sort(key=lambda x: -abs(x["spearman"]))
    return {
        "sample_n": int(len(df)),
        "high_pairs_abs_ge_0_80": high,
        "matrix": {c: {d: (None if pd.isna(corr.loc[c, d]) else float(corr.loc[c, d])) for d in corr.columns} for c in corr.columns},
    }


def _save(fig, name: str) -> str:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return str(path)


def make_plots(con, review_src, reviewer_src, product_src, cand_src, plots: list[str]) -> None:
    sns.set_theme(style="whitegrid")

    # 1 rating distribution
    d = _q(con, f"SELECT rating, COUNT(*) n FROM read_parquet('{review_src}') GROUP BY 1 ORDER BY 1")
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(d["rating"].astype(int).astype(str), d["n"])
    ax.set_xlabel("rating")
    ax.set_ylabel("count")
    ax.set_title("Review rating distribution (Track 1 corpus)")
    plots.append(_save(fig, "01_rating_distribution.png"))

    # 2 review length log1p histogram via buckets
    d = _q(
        con,
        f"""
        SELECT floor(ln(review_length_chars + 1)*4)/4 AS bin, COUNT(*) n
        FROM read_parquet('{review_src}')
        GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(d["bin"], d["n"], width=0.2)
    ax.set_xlabel("ln(review_length_chars + 1)")
    ax.set_ylabel("count")
    ax.set_title("Review length (log1p scale, SQL histogram)")
    plots.append(_save(fig, "02_review_length_log1p.png"))

    # 3 helpful votes log1p
    d = _q(
        con,
        f"""
        SELECT floor(ln(helpful_votes + 1)*4)/4 AS bin, COUNT(*) n
        FROM read_parquet('{review_src}')
        GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(d["bin"], d["n"], width=0.2)
    ax.set_xlabel("ln(helpful_votes + 1)")
    ax.set_ylabel("count")
    ax.set_title("Helpful votes (log1p scale, SQL histogram)")
    plots.append(_save(fig, "03_helpful_votes_log1p.png"))

    # 4 length by rating
    d = _q(
        con,
        f"""
        SELECT rating, AVG(review_length_chars) mean_len, MEDIAN(review_length_chars) median_len
        FROM read_parquet('{review_src}') GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    x = d["rating"].astype(int).astype(str)
    ax.plot(x, d["mean_len"], marker="o", label="mean")
    ax.plot(x, d["median_len"], marker="s", label="median")
    ax.set_xlabel("rating")
    ax.set_ylabel("review_length_chars")
    ax.set_title("Review length by rating")
    ax.legend()
    plots.append(_save(fig, "04_review_length_by_rating.png"))

    # 5 reviewer review_count log
    d = _q(
        con,
        f"""
        SELECT floor(ln(review_count)*5)/5 AS bin, COUNT(*) n
        FROM read_parquet('{reviewer_src}')
        GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(d["bin"], d["n"], width=0.15)
    ax.set_xlabel("ln(review_count)")
    ax.set_ylabel("reviewers")
    ax.set_title("Reviewer activity (log review_count)")
    plots.append(_save(fig, "05_reviewer_review_count_log.png"))

    # 6 reviewer avg rating
    d = _q(
        con,
        f"""
        SELECT round(avg_rating, 1) AS bin, COUNT(*) n
        FROM read_parquet('{reviewer_src}') GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(d["bin"], d["n"])
    ax.set_xlabel("avg_rating (0.1 bins)")
    ax.set_ylabel("reviewers")
    ax.set_title("Reviewer mean rating distribution")
    plots.append(_save(fig, "06_reviewer_avg_rating.png"))

    # 7 unique products log
    d = _q(
        con,
        f"""
        SELECT floor(ln(unique_products)*5)/5 AS bin, COUNT(*) n
        FROM read_parquet('{reviewer_src}') GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(d["bin"], d["n"], width=0.15)
    ax.set_xlabel("ln(unique_products)")
    ax.set_ylabel("reviewers")
    ax.set_title("Reviewer product diversity")
    plots.append(_save(fig, "07_reviewer_unique_products_log.png"))

    # 8 product review_count log
    d = _q(
        con,
        f"""
        SELECT floor(ln(review_count)*5)/5 AS bin, COUNT(*) n
        FROM read_parquet('{product_src}') GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(d["bin"], d["n"], width=0.15)
    ax.set_xlabel("ln(review_count)")
    ax.set_ylabel("products")
    ax.set_title("Product review-count distribution")
    plots.append(_save(fig, "08_product_review_count_log.png"))

    # 9 product avg rating
    d = _q(
        con,
        f"""
        SELECT round(avg_rating, 1) AS bin, COUNT(*) n
        FROM read_parquet('{product_src}') GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(d["bin"], d["n"])
    ax.set_xlabel("avg_rating (0.1 bins)")
    ax.set_ylabel("products")
    ax.set_title("Product mean rating distribution")
    plots.append(_save(fig, "09_product_avg_rating.png"))

    # 10 five_star_ratio concentration
    d = _q(
        con,
        f"""
        SELECT round(five_star_ratio, 2) AS bin, COUNT(*) n
        FROM read_parquet('{product_src}') GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(d["bin"], d["n"])
    ax.set_xlabel("five_star_ratio")
    ax.set_ylabel("products")
    ax.set_title("Product rating concentration (five-star share)")
    plots.append(_save(fig, "10_product_five_star_ratio.png"))

    # 11 review velocity log1p
    d = _q(
        con,
        f"""
        SELECT floor(ln(review_velocity + 1)*5)/5 AS bin, COUNT(*) n
        FROM read_parquet('{product_src}') GROUP BY 1 ORDER BY 1
        """,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(d["bin"], d["n"], width=0.15)
    ax.set_xlabel("ln(review_velocity + 1)")
    ax.set_ylabel("products")
    ax.set_title("Product review velocity (log1p)")
    plots.append(_save(fig, "11_product_review_velocity_log1p.png"))

    # 12 reviewer corr heatmap from sample spearman
    sample = con.execute(
        f"""
        SELECT review_count, unique_products, avg_rating, five_star_ratio, one_star_ratio,
               rating_entropy, extreme_rating_ratio, reviews_per_active_day,
               avg_review_length, average_helpfulness_per_review
        FROM read_parquet('{reviewer_src}') USING SAMPLE 250000
        """
    ).fetchdf()
    fig, ax = plt.subplots(figsize=(9, 7))
    sns.heatmap(sample.corr(method="spearman"), cmap="vlag", center=0, ax=ax, square=True)
    ax.set_title("Reviewer features Spearman (n=250,000 sample)")
    plots.append(_save(fig, "12_reviewer_corr_heatmap.png"))

    # 13 product corr heatmap sample
    sample = con.execute(
        f"""
        SELECT review_count, unique_reviewers, avg_rating, five_star_ratio, one_star_ratio,
               rating_entropy, review_velocity, avg_helpful_votes,
               avg_reviewer_review_count, avg_reviewer_product_diversity
        FROM read_parquet('{product_src}') USING SAMPLE 250000
        """
    ).fetchdf()
    fig, ax = plt.subplots(figsize=(9, 7))
    sns.heatmap(sample.corr(method="spearman"), cmap="vlag", center=0, ax=ax, square=True)
    ax.set_title("Product features Spearman (n=250,000 sample)")
    plots.append(_save(fig, "13_product_corr_heatmap.png"))

    # 14 candidate label distribution
    d = _q(con, f"SELECT candidate_label, COUNT(*) n FROM read_parquet('{cand_src}') GROUP BY 1 ORDER BY 1")
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(d["candidate_label"].astype(str), d["n"])
    ax.set_xlabel("heuristic candidate suspicious-review label")
    ax.set_ylabel("count")
    ax.set_title("Candidate label distribution (Track 2 only)")
    plots.append(_save(fig, "14_candidate_label_distribution.png"))

    # 15 candidate comparison
    d = _q(
        con,
        f"""
        SELECT candidate_label,
               AVG(overall) avg_rating,
               AVG(text_length) avg_text_len,
               AVG(helpful_votes) avg_helpful,
               AVG(bfr_CS) avg_bfr_CS,
               AVG(bfr_MNR) avg_bfr_MNR,
               AVG(bfr_RB) avg_bfr_RB
        FROM read_parquet('{cand_src}')
        GROUP BY 1 ORDER BY 1
        """,
    )
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8))
    x = d["candidate_label"].astype(str)
    axes[0].bar(x, d["avg_rating"])
    axes[0].set_title("mean rating")
    axes[1].bar(x, d["avg_bfr_CS"])
    axes[1].set_title("mean bfr_CS")
    axes[2].bar(x, d["avg_bfr_MNR"])
    axes[2].set_title("mean bfr_MNR")
    for ax in axes:
        ax.set_xlabel("candidate_label")
    fig.suptitle("Track 2 heuristic label: rating vs BFR (leakage-risk features)")
    plots.append(_save(fig, "15_candidate_label_comparison.png"))


def write_report(s: dict) -> None:
    rv, rr, pr = s["review"], s["reviewer"], s["product"]
    cat = s["category"]
    lab = s["candidate_label"]
    out = s["outliers"]
    rec = s["recommendations"]
    lines = []
    a = lines.append
    a("# Trust Feature EDA Report")
    a("")
    a("**Phase:** Exploratory analysis only. No models trained. No labels invented.")
    a("")
    a("## 1. Executive summary")
    a("")
    a(
        f"Track 1 feature tables cover **{rv['n']:,} reviews**, **{rr['n']:,} reviewers**, "
        f"and **{pr['n']:,} products**. Ratings are 5-star heavy "
        f"(share of 5-star reviews = {rv['rating_share']['5']:.4f}). "
        f"**{rr['n_single']:,} reviewers ({100*rr['n_single']/rr['n']:.2f}%) have exactly one review**; "
        "their `rating_std` is NULL by definition and was not filled. "
        f"Track 2 heuristic candidate suspicious-review label has "
        f"{lab['n_pos']:,} positives out of {lab['n']:,} "
        f"({100*lab['positive_rate']:.3f}%). It is **not** verified fraud ground truth "
        "and was **not** merged into Track 1 tables."
    )
    a("")
    a("`review_hour` is excluded: every Unix timestamp is midnight UTC; DuckDB `hour()` in IST is 5 for all rows.")
    a("")
    a("## 2. Dataset overview")
    a("")
    a("| Table | Rows | Columns | Source parquet |")
    a("|---|---:|---:|---|")
    a(f"| review_features | {rv['n']:,} | {rv['n_cols']} | `dataset/cleaned/features/review_features.parquet` |")
    a(f"| reviewer_features | {rr['n']:,} | {rr['n_cols']} | `dataset/cleaned/features/reviewer_features.parquet` |")
    a(f"| product_features | {pr['n']:,} | {pr['n_cols']} | `dataset/cleaned/features/product_features.parquet` |")
    a(f"| Track 2 analysis (separate) | {lab['n']:,} | — | `candidate_label/reviews_analysis.parquet` |")
    a("")
    a("## 3. Review-level analysis")
    a("")
    a("### Rating distribution")
    a("")
    for k, v in rv["rating_counts"].items():
        a(f"- rating {k}: {v:,} ({100*rv['rating_share'][k]:.2f}%)")
    a("")
    a("### Numeric summaries (selected)")
    a("")
    a("| Feature | n_non_null | mean | median | std | min | max | P1 | P50 | P99 |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for col in ["rating", "review_length_chars", "review_length_words", "summary_length", "helpful_votes", "product_rating_deviation", "reviewer_rating_deviation", "exact_text_copy_count"]:
        d = rv["describe"][col]
        stds = "None" if d["std"] is None else f"{d['std']:.4g}"
        a(
            f"| {col} | {d['n_non_null']:,} | {d['mean']:.4g} | {d['median']:.4g} | "
            f"{stds} | {d['min']:.4g} | {d['max']:.4g} | "
            f"{d['p01']:.4g} | {d['p50']:.4g} | {d['p99']:.4g} |"
        )
    a("")
    a("### Length and helpfulness by rating")
    a("")
    a("| rating | n | mean length | median length | mean helpful_votes |")
    a("|---:|---:|---:|---:|---:|")
    for row in rv["by_rating"]:
        a(
            f"| {int(row['rating'])} | {int(row['n']):,} | {row['mean_len']:.1f} | "
            f"{row['median_len']:.1f} | {row['mean_helpful']:.3f} |"
        )
    a("")
    a(
        f"Text available on {rv['text_available_share']*100:.4f}% of reviews; "
        f"summary available on {rv['summary_available_share']*100:.4f}%. "
        f"Exact-text copies (`is_exact_duplicate_text`): {rv['n_exact_dup_text']:,} reviews "
        f"({100*rv['n_exact_dup_text']/rv['n']:.2f}%). Hash collisions are possible; this is not a fraud label."
    )
    a("")
    a("Year range (meaningful date feature): "
      f"{rv['year_min']}–{rv['year_max']}. `review_hour` unused.")
    a("")
    a("## 4. Reviewer-level analysis")
    a("")
    a(
        f"- Single-review reviewers: **{rr['n_single']:,}** ({100*rr['n_single']/rr['n']:.2f}%). "
        f"`rating_std` NULL count = {rr['null_std']:,}, of which n=1: {rr['null_std_n1']:,}, n>1: {rr['null_std_n_gt1']:,}."
    )
    a(f"- Max review_count: **{rr['describe']['review_count']['max']:.0f}** (prolific ≠ fake).")
    a("")
    a("| Feature | n_non_null | mean | median | P95 | P99 | max |")
    a("|---|---:|---:|---:|---:|---:|---:|")
    for col in ["review_count", "unique_products", "avg_rating", "review_span_days", "reviews_per_active_day", "five_star_ratio", "one_star_ratio", "extreme_rating_ratio", "rating_entropy"]:
        d = rr["describe"][col]
        a(
            f"| {col} | {d['n_non_null']:,} | {d['mean']:.4g} | {d['median']:.4g} | "
            f"{d['p95']:.4g} | {d['p99']:.4g} | {d['max']:.4g} |"
        )
    a("")
    a("### Highest-activity reviewers (EDA only, not fraud labels)")
    a("")
    a("| review_count | unique_products | avg_rating | span_days | five_star_ratio |")
    a("|---:|---:|---:|---:|---:|")
    for row in rr["top_activity"]:
        a(
            f"| {int(row['review_count'])} | {int(row['unique_products'])} | "
            f"{row['avg_rating']:.3f} | {row['review_span_days']:.1f} | {row['five_star_ratio']:.3f} |"
        )
    a("")
    a("## 5. Product-level analysis")
    a("")
    a(
        f"- Products with one review: **{pr['n_single']:,}** ({100*pr['n_single']/pr['n']:.2f}%). "
        f"Max review_count: **{pr['describe']['review_count']['max']:.0f}**."
    )
    a("")
    a("| Feature | mean | median | P95 | P99 | max |")
    a("|---|---:|---:|---:|---:|---:|")
    for col in ["review_count", "unique_reviewers", "avg_rating", "five_star_ratio", "one_star_ratio", "rating_entropy", "review_velocity"]:
        d = pr["describe"][col]
        a(
            f"| {col} | {d['mean']:.4g} | {d['median']:.4g} | {d['p95']:.4g} | {d['p99']:.4g} | {d['max']:.4g} |"
        )
    a("")
    a(
        f"Products with ≥10 reviews and five_star_ratio ≥ 0.95 (rating-concentrated, **not** fraud): "
        f"{pr['n_rating_concentrated']:,}."
    )
    a(
        f"Products with review_count > P99 (high-activity): {pr['n_high_activity']:,}."
    )
    a("")
    a("## 6. Category analysis")
    a("")
    a("| category | reviews | mean rating | median length | mean helpful |")
    a("|---|---:|---:|---:|---:|")
    for row in cat:
        a(
            f"| {row['category']} | {int(row['n']):,} | {row['mean_rating']:.3f} | "
            f"{row['median_len']:.0f} | {row['mean_helpful']:.3f} |"
        )
    a("")
    a("Category volume and rating mix differ. That is expected Amazon-category structure, not evidence of fraud.")
    a("")
    a("## 7. Correlation analysis")
    a("")
    a("Pearson correlations for reviewer/product (and review) tables were computed in DuckDB (`corr`; pairwise nulls skipped). "
      "Percentiles on large tables use DuckDB `approx_quantile`. "
      "Spearman heatmaps use a **250,000-row random sample**.")
    a("")
    a("### Reviewer Pearson |r| ≥ 0.80")
    a("")
    for p in s["corr"]["reviewer_pearson"]["high_pairs_abs_ge_0_80"][:20]:
        a(f"- {p['a']} vs {p['b']}: {p['pearson']:.3f}")
    a("")
    a("### Product Pearson |r| ≥ 0.80")
    a("")
    for p in s["corr"]["product_pearson"]["high_pairs_abs_ge_0_80"][:20]:
        a(f"- {p['a']} vs {p['b']}: {p['pearson']:.3f}")
    a("")
    a("### Review Pearson |r| ≥ 0.80")
    a("")
    rp = s["corr"]["review_pearson"]["high_pairs_abs_ge_0_80"]
    if rp:
        for p in rp[:20]:
            a(f"- {p['a']} vs {p['b']}: {p['pearson']:.3f}")
    else:
        a("- No review-level pairs with |r| ≥ 0.80 among the selected numeric columns.")
    a("")
    a("## 8. Outlier analysis")
    a("")
    a("Descriptive EDA categories only — **not fraud labels**.")
    a("")
    a("- HIGH_ACTIVITY reviewer: `review_count` > P95 "
      f"({out['reviewer_p95_count']:.0f}) → {out['reviewer_high_activity']:,}")
    a("- EXTREME_ACTIVITY reviewer: `review_count` > P99 "
      f"({out['reviewer_p99_count']:.0f}) → {out['reviewer_extreme_activity']:,}")
    a("- UNUSUAL_RATING_PATTERN reviewer: `review_count` ≥ 5 and `extreme_rating_ratio` ≥ 0.95 → "
      f"{out['reviewer_unusual_rating']:,}")
    a("- UNUSUAL_REVIEW_LENGTH review: `review_length_chars` < P1 or > P99 among all reviews → "
      f"{out['review_unusual_length']:,}")
    a("- HIGH_ACTIVITY product: `review_count` > P99 → "
      f"{out['product_high_activity']:,}")
    a("- Rating-concentrated product: `review_count` ≥ 10 and `five_star_ratio` ≥ 0.95 → "
      f"{out['product_rating_concentrated']:,}")
    a("")
    a("IQR fences (P25−1.5·IQR, P75+1.5·IQR) for reviewer `review_count` are also stored in `eda_summary.json`. "
      "Activity is heavy-tailed; IQR flags a large share of reviewers with more than a few reviews. "
      "Percentile flags are more interpretable here.")
    a("")
    a("## 9. Candidate-label analysis (Track 2 only)")
    a("")
    a("This is a **heuristic candidate suspicious-review label**, not verified fake-review ground truth. "
      "False positives and false negatives are expected. It lives in `separate.csv` / `candidate_label/` "
      "and was not joined onto the 21.9M Track 1 feature tables.")
    a("")
    a(f"- n = {lab['n']:,}; label=1: {lab['n_pos']:,}; label=0: {lab['n_neg']:,}; rate = {lab['positive_rate']:.5f}")
    a("")
    a("| metric | label=0 mean | label=1 mean |")
    a("|---|---:|---:|")
    for k, v in lab["means_by_label"].items():
        a(f"| {k} | {v['0']:.4g} | {v['1']:.4g} |")
    a("")
    a("`bfr_*` gaps (especially CS, MNR, RB) are large relative to rating/text gaps. "
      "That is consistent with the label being **related to BehaviouralFeatureResult**, i.e. **TARGET LEAKAGE RISK** "
      "if BFR columns are used to predict this label.")
    a("")
    a("## 10. Leakage risks")
    a("")
    a("- **BFR vs Track 2 candidate_label:** TARGET LEAKAGE RISK. Do not use `bfr_*` as features for that label without proving independent labeling.")
    a("- **`class` / `rating_polarity` / `rating`:** `class` is a recode of stars. Predicting class from rating is tautological.")
    a("- **Deviations:** `product_rating_deviation` uses the product mean including the current review (small leakage for tiny products). Prefer leave-one-out if used in a model.")
    a("- **Reviewer aggregates on the same row:** `reviewer_rating_deviation` includes the current rating in the reviewer mean.")
    a("- **`review_hour`:** invalid as a behavior feature.")
    a("")
    a("## 11. Important observations")
    a("")
    a("- Amazon-typical 5-star skew at review, reviewer, and product levels.")
    a("- Reviewer and product activity are highly skewed (median review_count near 1; long tail).")
    a("- Length and helpfulness vary by star rating (see tables); this is consistent with sentiment, not proof of spam.")
    a("- A minority of reviews share exact text hashes (possible templates or collisions).")
    a("")
    a("## 12. Limitations")
    a("")
    a("- Data end in 2014. Day-level timestamps only.")
    a("- Combined corpus de-duplicated with keep-first category.")
    a("- Exact-text duplicates use hashes.")
    a("- Spearman heatmaps are sampled.")
    a("- No verified trust/fraud labels on Track 1.")
    a("")
    a("## 13. Features recommended for later ML")
    a("")
    a("Depends on the **target**. For unsupervised / reviewer-risk profiling (no Track 2 label):")
    a("")
    for item in rec["A"]:
        a(f"- **{item['name']}:** {item['why']}")
    a("")
    a("## 14. Features NOT recommended (or restricted)")
    a("")
    a("### B — investigate")
    for item in rec["B"]:
        a(f"- **{item['name']}:** {item['why']}")
    a("")
    a("### C — redundant")
    for item in rec["C"]:
        a(f"- **{item['name']}:** {item['why']}")
    a("")
    a("### D — leakage-risk")
    for item in rec["D"]:
        a(f"- **{item['name']}:** {item['why']}")
    a("")
    a("### E — invalid/unusable")
    for item in rec["E"]:
        a(f"- **{item['name']}:** {item['why']}")
    a("")
    a("## 15. Next-step recommendation")
    a("")
    a("Define the modeling objective explicitly: (1) unsupervised/descriptive trust risk scoring on Track 1 features, "
      "and/or (2) a clearly caveated experiment on the Track 2 heuristic label **without BFR features**, "
      "split by `reviewerID`. Do not train a dashboard model that reports “trust accuracy” on `class` or on BFR-derived labels.")
    a("")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    t0 = time.time()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    protected = [COMBINED, REVIEWERS, REVIEW_OUT, REVIEWER_OUT, PRODUCT_OUT]
    protected += sorted((COMBINED.parent / "reviews_by_category").glob("*.parquet"))
    protected += list(RAW_DIR.glob("*.csv"))
    if CANDIDATE_ANALYSIS.exists():
        protected.append(CANDIDATE_ANALYSIS)
    before = snapshot_mtimes(protected)

    con = connect("4GB")
    con.execute(f"SET temp_directory='{sql_path(TMP)}'")
    TMP.mkdir(parents=True, exist_ok=True)
    rsrc, vsrc, psrc = sql_path(REVIEW_OUT), sql_path(REVIEWER_OUT), sql_path(PRODUCT_OUT)
    csrc = sql_path(CANDIDATE_ANALYSIS)

    print("Describing review columns...", flush=True)
    review_desc = describe_cols(con, rsrc, REVIEW_NUM)
    rating_df = _q(con, f"SELECT rating, COUNT(*) n FROM read_parquet('{rsrc}') GROUP BY 1 ORDER BY 1")
    n_review = parquet_rows(REVIEW_OUT)
    rating_counts = {str(int(r.rating)): int(r.n) for r in rating_df.itertuples()}
    rating_share = {k: v / n_review for k, v in rating_counts.items()}
    by_rating = _q(
        con,
        f"""
        SELECT rating, COUNT(*) n,
               AVG(review_length_chars) mean_len, MEDIAN(review_length_chars) median_len,
               AVG(helpful_votes) mean_helpful
        FROM read_parquet('{rsrc}') GROUP BY 1 ORDER BY 1
        """,
    ).to_dict(orient="records")
    flags = con.execute(
        f"""
        SELECT AVG(CASE WHEN text_available THEN 1.0 ELSE 0.0 END) text_share,
               AVG(CASE WHEN summary_available THEN 1.0 ELSE 0.0 END) summary_share,
               SUM(CASE WHEN is_exact_duplicate_text THEN 1 ELSE 0 END) n_dup,
               MIN(review_year) ymin, MAX(review_year) ymax
        FROM read_parquet('{rsrc}')
        """
    ).fetchone()

    print("Describing reviewer columns...", flush=True)
    reviewer_desc = describe_cols(con, vsrc, REVIEWER_NUM)
    n_reviewer = parquet_rows(REVIEWER_OUT)
    rv_extra = con.execute(
        f"""
        SELECT
          SUM(CASE WHEN review_count = 1 THEN 1 ELSE 0 END) n_single,
          COUNT(*) FILTER (WHERE rating_std IS NULL) null_std,
          COUNT(*) FILTER (WHERE rating_std IS NULL AND review_count = 1) null_std_n1,
          COUNT(*) FILTER (WHERE rating_std IS NULL AND review_count > 1) null_std_n_gt1
        FROM read_parquet('{vsrc}')
        """
    ).fetchone()
    top_act = _q(
        con,
        f"""
        SELECT review_count, unique_products, avg_rating, review_span_days, five_star_ratio
        FROM read_parquet('{vsrc}')
        ORDER BY review_count DESC
        LIMIT 10
        """,
    ).to_dict(orient="records")

    print("Describing product columns...", flush=True)
    product_desc = describe_cols(con, psrc, PRODUCT_NUM)
    n_product = parquet_rows(PRODUCT_OUT)
    p95 = product_desc["review_count"]["p95"]
    p99 = product_desc["review_count"]["p99"]
    pr_extra = con.execute(
        f"""
        SELECT
          SUM(CASE WHEN review_count = 1 THEN 1 ELSE 0 END) n_single,
          SUM(CASE WHEN review_count > {p99} THEN 1 ELSE 0 END) n_high,
          SUM(CASE WHEN review_count >= 10 AND five_star_ratio >= 0.95 THEN 1 ELSE 0 END) n_conc
        FROM read_parquet('{psrc}')
        """
    ).fetchone()

    print("Category aggregates...", flush=True)
    category = _q(
        con,
        f"""
        SELECT category, COUNT(*) n, AVG(rating) mean_rating,
               MEDIAN(review_length_chars) median_len, AVG(helpful_votes) mean_helpful
        FROM read_parquet('{rsrc}')
        GROUP BY 1 ORDER BY n DESC
        """,
    ).to_dict(orient="records")

    print("Correlations (full Pearson in DuckDB)...", flush=True)
    review_pearson = pearson_matrix(con, rsrc, [c for c in REVIEW_NUM if c != "review_hour"])
    reviewer_pearson = pearson_matrix(
        con, vsrc, [c for c in REVIEWER_NUM if c not in {"rating_variance"}]
    )
    product_pearson = pearson_matrix(con, psrc, PRODUCT_NUM)
    print("Spearman samples...", flush=True)
    reviewer_spear = spearman_sample(
        con,
        vsrc,
        [
            "review_count",
            "unique_products",
            "avg_rating",
            "five_star_ratio",
            "one_star_ratio",
            "rating_entropy",
            "extreme_rating_ratio",
            "reviews_per_active_day",
            "avg_review_length",
            "average_helpfulness_per_review",
        ],
    )
    product_spear = spearman_sample(
        con,
        psrc,
        [
            "review_count",
            "unique_reviewers",
            "avg_rating",
            "five_star_ratio",
            "one_star_ratio",
            "rating_entropy",
            "review_velocity",
            "avg_helpful_votes",
            "avg_reviewer_review_count",
            "avg_reviewer_product_diversity",
        ],
    )

    rp95 = review_desc["review_length_chars"]["p95"]
    rp01 = review_desc["review_length_chars"]["p01"]
    rp99 = review_desc["review_length_chars"]["p99"]
    rvp95 = reviewer_desc["review_count"]["p95"]
    rvp99 = reviewer_desc["review_count"]["p99"]
    q = reviewer_desc["review_count"]
    iqr = q["p75"] - q["p25"]
    iqr_hi = q["p75"] + 1.5 * iqr
    outliers = con.execute(
        f"""
        SELECT
          (SELECT COUNT(*) FROM read_parquet('{vsrc}') WHERE review_count > {rvp95}) reviewer_high,
          (SELECT COUNT(*) FROM read_parquet('{vsrc}') WHERE review_count > {rvp99}) reviewer_ext,
          (SELECT COUNT(*) FROM read_parquet('{vsrc}') WHERE review_count >= 5 AND extreme_rating_ratio >= 0.95) reviewer_pat,
          (SELECT COUNT(*) FROM read_parquet('{vsrc}') WHERE review_count > {iqr_hi}) reviewer_iqr,
          (SELECT COUNT(*) FROM read_parquet('{rsrc}') WHERE review_length_chars < {rp01} OR review_length_chars > {rp99}) review_len,
          (SELECT COUNT(*) FROM read_parquet('{psrc}') WHERE review_count > {p99}) product_high
        """
    ).fetchone()

    print("Candidate label (Track 2)...", flush=True)
    lab_n = parquet_rows(CANDIDATE_ANALYSIS)
    lab_counts = _q(con, f"SELECT candidate_label, COUNT(*) n FROM read_parquet('{csrc}') GROUP BY 1")
    cmap = {int(r.candidate_label): int(r.n) for r in lab_counts.itertuples()}
    mean_cols = ["overall", "text_length", "helpful_votes", "bfr_CS", "bfr_MNR", "bfr_RB", "bfr_NR", "class_raw"]
    means = {}
    for col in mean_cols:
        dd = _q(
            con,
            f"SELECT candidate_label, AVG({col}) m FROM read_parquet('{csrc}') GROUP BY 1",
        )
        means[col] = {str(int(r.candidate_label)): float(r.m) if r.m is not None else None for r in dd.itertuples()}

    print("Plots...", flush=True)
    plots: list[str] = []
    make_plots(con, rsrc, vsrc, psrc, csrc, plots)

    recommendations = {
        "A": [
            {"name": "review_length_chars / review_length_words", "why": "Supported by source text; varies by rating; usable for NLP/length risk without being a recode of the target."},
            {"name": "helpful_votes / helpful_ratio", "why": "Observed engagement; biased by exposure but not a recode of class/label."},
            {"name": "reviewer review_count, unique_products, reviews_per_active_day", "why": "Directly computed activity/diversity; useful for unsupervised risk profiling. Not fraud GT."},
            {"name": "five_star_ratio, one_star_ratio, rating_entropy, extreme_rating_ratio", "why": "Describe rating concentration; interpretable. Handle n=1 separately."},
            {"name": "product review_count, unique_reviewers, five_star_ratio, review_velocity", "why": "Product-level volume and concentration for later product trust scoring."},
            {"name": "is_exact_duplicate_text / exact_text_copy_count", "why": "Template/copy signal; hash-limited; not a label."},
        ],
        "B": [
            {"name": "product_rating_deviation, reviewer_rating_deviation", "why": "Useful outlier signal but include the current row in the mean; consider leave-one-out before supervised use."},
            {"name": "avg_reviewer_rating_variability on products", "why": "AVG skips NULL std (single-review reviewers omitted)."},
            {"name": "review_year / month / day_of_week", "why": "Calendar effects; not time-of-day. Check drift before using in a classifier."},
            {"name": "Track 2 candidate_label as target", "why": "Only possible rare-event target; unverified; split by reviewerID if used."},
        ],
        "C": [
            {"name": "rating_variance vs rating_std", "why": "Variance is std squared (Pearson ~1 when both non-null)."},
            {"name": "review_length_words vs review_length_chars", "why": "Both measure length; likely high correlation."},
            {"name": "unique_reviewers vs review_count on products", "why": "Often nearly 1:1 if few repeat reviewers per product."},
            {"name": "five_star_ratio vs avg_rating", "why": "Mechanically related on a 1–5 scale."},
            {"name": "n_rating_k histogram vs ratios", "why": "Ratios are normalized histograms."},
        ],
        "D": [
            {"name": "bfr_* predicting Track 2 candidate_label", "why": "TARGET LEAKAGE RISK: large mean gaps vs label; likely used to construct or is collinear with the heuristic."},
            {"name": "overall/class as trust target", "why": "class is 4–5 vs 1–3 stars."},
            {"name": "candidate_label as a Track 1 feature", "why": "Different corpus; would leak if used both as feature and target."},
        ],
        "E": [
            {"name": "review_hour", "why": "Midnight UTC stored; hour=5 in IST for all rows; not genuine time-of-day."},
            {"name": "verified_purchase", "why": "Column does not exist."},
            {"name": "rating_std filled with 0 for n=1", "why": "Would fabricate certainty; leave NULL."},
            {"name": "IDs (review_id, reviewerID, asin) as numeric model features", "why": "Identifiers, not behavior."},
        ],
    }

    elapsed = round(time.time() - t0, 2)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": elapsed,
        "method": "DuckDB aggregations on parquet; plots from SQL histograms or 250k samples; no pandas load of 21.9M reviews",
        "review": {
            "n": n_review,
            "n_cols": 24,
            "describe": review_desc,
            "rating_counts": rating_counts,
            "rating_share": rating_share,
            "by_rating": by_rating,
            "text_available_share": float(flags[0]),
            "summary_available_share": float(flags[1]),
            "n_exact_dup_text": int(flags[2]),
            "year_min": int(flags[3]),
            "year_max": int(flags[4]),
        },
        "reviewer": {
            "n": n_reviewer,
            "n_cols": 32,
            "describe": reviewer_desc,
            "n_single": int(rv_extra[0]),
            "null_std": int(rv_extra[1]),
            "null_std_n1": int(rv_extra[2]),
            "null_std_n_gt1": int(rv_extra[3]),
            "top_activity": top_act,
        },
        "product": {
            "n": n_product,
            "n_cols": 25,
            "describe": product_desc,
            "n_single": int(pr_extra[0]),
            "n_high_activity": int(pr_extra[1]),
            "n_rating_concentrated": int(pr_extra[2]),
        },
        "category": category,
        "corr": {
            "review_pearson": review_pearson,
            "reviewer_pearson": reviewer_pearson,
            "product_pearson": product_pearson,
            "reviewer_spearman_sample": reviewer_spear,
            "product_spearman_sample": product_spear,
        },
        "outliers": {
            "methodology": "Percentile and rule-based EDA tags only; not fraud labels. IQR on reviewer review_count also counted.",
            "reviewer_p95_count": rvp95,
            "reviewer_p99_count": rvp99,
            "reviewer_high_activity": int(outliers[0]),
            "reviewer_extreme_activity": int(outliers[1]),
            "reviewer_unusual_rating": int(outliers[2]),
            "reviewer_iqr_above_upper": int(outliers[3]),
            "reviewer_iqr_upper_fence": iqr_hi,
            "review_unusual_length": int(outliers[4]),
            "review_length_p01": rp01,
            "review_length_p99": rp99,
            "product_high_activity": int(outliers[5]),
            "product_rating_concentrated": int(pr_extra[2]),
        },
        "candidate_label": {
            "n": lab_n,
            "n_pos": cmap.get(1, 0),
            "n_neg": cmap.get(0, 0),
            "positive_rate": cmap.get(1, 0) / lab_n if lab_n else None,
            "means_by_label": means,
            "status": "heuristic candidate suspicious-review label; NOT verified fraud ground truth",
        },
        "leakage_warnings": [
            "TARGET LEAKAGE RISK: bfr_* vs Track 2 candidate_label",
            "class/rating_polarity is a recode of overall",
            "review_hour is not genuine time-of-day",
            "deviations include the current row in group means",
        ],
        "recommendations": recommendations,
        "excluded_features": ["review_hour", "verified_purchase", "bfr_* as predictors of candidate_label", "class as trust target"],
        "plots": plots,
        "n_plots": len(plots),
    }

    write_report(summary)
    EDA_JSON.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    con.close()
    cleanup_tmp()
    import shutil

    shutil.rmtree(TMP, ignore_errors=True)

    after = snapshot_mtimes(protected)
    changed = [p for p, m0 in before.items() if abs(after.get(p, -1) - m0) >= 0.5]
    print("protected_mtime_changed", changed, flush=True)
    print(f"EDA done in {elapsed}s plots={len(plots)}", flush=True)
    print("Wrote", REPORT_MD, "and", EDA_JSON, flush=True)


if __name__ == "__main__":
    main()
