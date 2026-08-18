"""
TRACK 2 — Candidate-label experiment from separate.csv only.

Does not modify dataset/csv_files/.
Does not merge with Track 1.
Does not treat label as confirmed trust ground truth.
BehaviouralFeatureResult is parsed for analysis only and excluded from ml_ready.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cleaning_common import (  # noqa: E402
    BFR_KEYS,
    CLEANED_DIR,
    RAW_DIR,
    apply_review_dtypes,
    clean_review_frame,
    miss_mask,
    parse_bfr_column,
)

OUT_DIR = CLEANED_DIR / "candidate_label"
CHUNKSIZE = 150_000
RAW_NAME = "separate.csv"

ANALYSIS_COLS = [
    "source_file",
    "reviewerID",
    "asin",
    "helpful_votes",
    "helpful_total",
    "helpful_ratio",
    "reviewText",
    "text_length",
    "text_missing",
    "text_very_short",
    "summary",
    "summary_missing",
    "overall",
    "rating_polarity",
    "unixReviewTime",
    "review_datetime_utc",
    "category",
    "class_raw",
    "class_missing",
    "candidate_label",
] + [f"bfr_{k}" for k in BFR_KEYS]

ML_COLS = [
    "reviewerID",
    "asin",
    "category",
    "overall",
    "helpful_votes",
    "helpful_total",
    "helpful_ratio",
    "reviewText",
    "text_length",
    "text_missing",
    "text_very_short",
    "summary",
    "unixReviewTime",
    "review_datetime_utc",
    "candidate_label",
]


def write_chunk(writer: pq.ParquetWriter | None, df: pd.DataFrame, path: Path) -> pq.ParquetWriter:
    table = pa.Table.from_pandas(df, preserve_index=False)
    if writer is None:
        writer = pq.ParquetWriter(path, table.schema, compression="snappy")
    writer.write_table(table)
    return writer


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float | None:
    mask = np.isfinite(y_score) & np.isfinite(y_true)
    y = y_true[mask]
    s = y_score[mask]
    if y.size == 0 or y.min() == y.max():
        return None
    order = np.argsort(s)
    y = y[order]
    n_pos = float(y.sum())
    n_neg = float(len(y) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = np.arange(1, len(y) + 1, dtype=float)
    # average ranks for ties
    # simple AUC via Mann-Whitney
    pos_ranks = ranks[y == 1]
    auc = (pos_ranks.sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return float(auc)


def main() -> None:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / RAW_NAME
    analysis_path = OUT_DIR / "reviews_analysis.parquet"
    ml_path = OUT_DIR / "ml_ready.parquet"

    analysis_writer = None
    ml_writer = None
    seen: set[int] = set()
    n_in = 0
    n_written = 0
    n_dup_dropped = 0
    label_counts = {0: 0, 1: 0, "invalid": 0}
    class_label = {}
    overall_label = {}
    sample_rows = []

    # BFR vs label running stats: sum0, sum1, sumsq0, sumsq1, n0, n1
    bfr_stats = {k: [0.0, 0.0, 0.0, 0.0, 0, 0] for k in BFR_KEYS}

    print(f"=== Track 2 cleaning {RAW_NAME} ===", flush=True)
    reader = pd.read_csv(
        raw_path,
        chunksize=CHUNKSIZE,
        dtype=str,
        keep_default_na=False,
        na_values=[],
        encoding="utf-8",
        encoding_errors="replace",
        engine="c",
    )
    for chunk in reader:
        n_in += len(chunk)
        cleaned = apply_review_dtypes(clean_review_frame(chunk, RAW_NAME))
        bfr = parse_bfr_column(chunk["BehaviouralFeatureResult"])
        for c in bfr.columns:
            cleaned[c] = bfr[c].to_numpy()

        lab_missing = miss_mask(chunk["label"])
        lab = pd.to_numeric(chunk["label"], errors="coerce")
        cleaned["candidate_label"] = lab.where(~lab_missing, np.nan)

        hashes = cleaned["row_key_hash"].to_numpy()
        keep = np.empty(len(cleaned), dtype=bool)
        dropped = 0
        for i, h in enumerate(hashes.tolist()):
            if h in seen:
                keep[i] = False
                dropped += 1
            else:
                seen.add(h)
                keep[i] = True
        n_dup_dropped += dropped
        cleaned = cleaned.loc[keep].copy()
        n_written += len(cleaned)

        for v in cleaned["candidate_label"].to_numpy():
            if v == 0 or v == 0.0:
                label_counts[0] += 1
            elif v == 1 or v == 1.0:
                label_counts[1] += 1
            else:
                label_counts["invalid"] += 1

        tmp = cleaned.assign(
            _c=cleaned["class_raw"].fillna(-1).astype(str),
            _l=cleaned["candidate_label"].astype(str),
            _o=cleaned["overall"].astype(str),
        )
        for (c, l), n in tmp.groupby(["_c", "_l"]).size().items():
            class_label[f"{c}|{l}"] = class_label.get(f"{c}|{l}", 0) + int(n)
        for (o, l), n in tmp.groupby(["_o", "_l"]).size().items():
            overall_label[f"{o}|{l}"] = overall_label.get(f"{o}|{l}", 0) + int(n)

        y = cleaned["candidate_label"].to_numpy(dtype=float)
        for k in BFR_KEYS:
            x = cleaned[f"bfr_{k}"].to_numpy(dtype=float)
            m0 = (y == 0) & np.isfinite(x)
            m1 = (y == 1) & np.isfinite(x)
            st = bfr_stats[k]
            if m0.any():
                v0 = x[m0]
                st[0] += float(v0.sum())
                st[2] += float((v0 ** 2).sum())
                st[4] += int(m0.sum())
            if m1.any():
                v1 = x[m1]
                st[1] += float(v1.sum())
                st[3] += float((v1 ** 2).sum())
                st[5] += int(m1.sum())

        if len(sample_rows) < 4000:
            sample_rows.append(cleaned.head(800))

        analysis_writer = write_chunk(analysis_writer, cleaned[ANALYSIS_COLS], analysis_path)
        ml_writer = write_chunk(ml_writer, cleaned[ML_COLS], ml_path)
        print(f"  {n_in:,} read, {n_written:,} kept, {n_dup_dropped:,} dups dropped", flush=True)

    if analysis_writer:
        analysis_writer.close()
    if ml_writer:
        ml_writer.close()

    if sample_rows:
        pd.concat(sample_rows, ignore_index=True).head(4000).to_csv(
            OUT_DIR / "reviews_analysis_sample.csv", index=False
        )

    print("Computing BFR vs label AUCs on analysis parquet...", flush=True)
    aucs = {}
    pf = pq.ParquetFile(analysis_path)
    y_all = []
    scores = {k: [] for k in BFR_KEYS}
    overall_all = []
    for batch in pf.iter_batches(batch_size=200_000, columns=["candidate_label", "overall"] + [f"bfr_{k}" for k in BFR_KEYS]):
        d = batch.to_pandas()
        y_all.append(d["candidate_label"].to_numpy(dtype=float))
        overall_all.append(d["overall"].to_numpy(dtype=float))
        for k in BFR_KEYS:
            scores[k].append(d[f"bfr_{k}"].to_numpy(dtype=float))
    y = np.concatenate(y_all)
    aucs["overall"] = roc_auc(y, np.concatenate(overall_all))
    for k in BFR_KEYS:
        aucs[f"bfr_{k}"] = roc_auc(y, np.concatenate(scores[k]))

    bfr_summary = {}
    for k, st in bfr_stats.items():
        n0, n1 = st[4], st[5]
        mean0 = st[0] / n0 if n0 else None
        mean1 = st[1] / n1 if n1 else None
        bfr_summary[k] = {
            "n_label0": n0,
            "n_label1": n1,
            "mean_label0": mean0,
            "mean_label1": mean1,
            "mean_diff": (None if mean0 is None or mean1 is None else mean1 - mean0),
        }

    n0, n1 = label_counts[0], label_counts[1]
    manifest = {
        "track": 2,
        "source": RAW_NAME,
        "part_csv_included": False,
        "part_csv_reason": "part.csv is a subset of separate.csv and has no label=1 rows; not an independent dataset",
        "rows_read": n_in,
        "rows_written_after_dedup": n_written,
        "duplicate_triples_dropped": n_dup_dropped,
        "candidate_label_counts": {"0": n0, "1": n1, "invalid_or_missing": label_counts["invalid"]},
        "positive_rate": (n1 / (n0 + n1)) if (n0 + n1) else None,
        "class_by_label": class_label,
        "overall_by_label": overall_label,
        "bfr_means_by_label": bfr_summary,
        "univariate_auc_vs_candidate_label": aucs,
        "ml_ready_excludes": [
            "BehaviouralFeatureResult and all bfr_* columns",
            "class_raw / class_missing",
            "reviewerName",
            "mongo_id",
            "row_key_hash",
        ],
        "target_name": "candidate_label",
        "target_status": "UNVERIFIED candidate; not confirmed trust ground truth",
        "elapsed_sec": round(time.time() - t0, 2),
        "analysis_parquet": str(analysis_path),
        "ml_ready_parquet": str(ml_path),
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ["rows_written_after_dedup", "candidate_label_counts", "positive_rate", "univariate_auc_vs_candidate_label"]}, indent=2, default=str))
    print(f"Track 2 complete in {manifest['elapsed_sec']}s", flush=True)


if __name__ == "__main__":
    main()
