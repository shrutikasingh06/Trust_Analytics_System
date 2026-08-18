"""
TRACK 1 — Behavior / EDA corpus from the six category review CSVs.

Does not modify dataset/csv_files/.
Does not use class/overall as trust ground truth.
Does not include part.csv or separate.csv.
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
    CATEGORY_FILES,
    CLEANED_DIR,
    RAW_DIR,
    apply_review_dtypes,
    clean_review_frame,
)

OUT_DIR = CLEANED_DIR / "behavior_eda"
CAT_DIR = OUT_DIR / "reviews_by_category"
CHUNKSIZE = 200_000

KEEP_COLS = [
    "source_file",
    "mongo_id",
    "reviewerID",
    "asin",
    "reviewerName",
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
]


def parquet_writer_for(df: pd.DataFrame, path: Path) -> pq.ParquetWriter:
    table = pa.Table.from_pandas(df, preserve_index=False)
    return pq.ParquetWriter(path, table.schema, compression="snappy")


def write_chunk(writer: pq.ParquetWriter | None, df: pd.DataFrame, path: Path) -> pq.ParquetWriter:
    table = pa.Table.from_pandas(df, preserve_index=False)
    if writer is None:
        writer = pq.ParquetWriter(path, table.schema, compression="snappy")
    writer.write_table(table)
    return writer


def main() -> None:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CAT_DIR.mkdir(parents=True, exist_ok=True)
    sample_dir = OUT_DIR / "samples"
    sample_dir.mkdir(exist_ok=True)

    combined_path = OUT_DIR / "reviews_combined_dedup.parquet"
    seen_keys: set[int] = set()
    combined_writer = None
    sample_frames: list[pd.DataFrame] = []

    stats = {
        "track": 1,
        "processing_order": CATEGORY_FILES,
        "dedup_rule": "combined file keeps first (reviewerID, asin, unixReviewTime) in processing_order",
        "files": {},
        "combined_rows_written": 0,
        "combined_rows_dropped_as_cross_file_duplicates": 0,
    }

    for fname in CATEGORY_FILES:
        path = RAW_DIR / fname
        print(f"=== Track 1 cleaning {fname} ===", flush=True)
        cat_name = fname.replace(".csv", "")
        cat_out = CAT_DIR / f"{cat_name}.parquet"
        cat_writer = None
        n_in = 0
        n_cat_written = 0
        n_dup_dropped = 0
        n_text_missing = 0
        n_short = 0

        reader = pd.read_csv(
            path,
            chunksize=CHUNKSIZE,
            dtype=str,
            keep_default_na=False,
            na_values=[],
            encoding="utf-8",
            encoding_errors="replace",
            engine="c",
        )
        sampled = False
        for chunk in reader:
            n_in += len(chunk)
            cleaned = apply_review_dtypes(clean_review_frame(chunk, fname))
            n_text_missing += int(cleaned["text_missing"].sum())
            n_short += int(cleaned["text_very_short"].sum())

            cat_part = cleaned[KEEP_COLS]
            cat_writer = write_chunk(cat_writer, cat_part, cat_out)
            n_cat_written += len(cat_part)

            if not sampled:
                sample_frames.append(cat_part.head(1500).copy())
                sampled = True

            hashes = cleaned["row_key_hash"].to_numpy()
            keep_mask = np.empty(len(cleaned), dtype=bool)
            dropped = 0
            for i, h in enumerate(hashes.tolist()):
                if h in seen_keys:
                    keep_mask[i] = False
                    dropped += 1
                else:
                    seen_keys.add(h)
                    keep_mask[i] = True
            n_dup_dropped += dropped
            kept = cat_part.loc[keep_mask]
            if len(kept):
                combined_writer = write_chunk(combined_writer, kept, combined_path)
                stats["combined_rows_written"] += len(kept)
            stats["combined_rows_dropped_as_cross_file_duplicates"] += dropped

            if n_in % 600_000 < CHUNKSIZE:
                print(f"  {fname}: {n_in:,} read, combined kept {stats['combined_rows_written']:,}", flush=True)

        if cat_writer is not None:
            cat_writer.close()
        stats["files"][fname] = {
            "rows_read": n_in,
            "rows_written_category_parquet": n_cat_written,
            "cross_file_duplicates_not_copied_to_combined": n_dup_dropped,
            "text_missing": n_text_missing,
            "text_very_short": n_short,
            "output": str(cat_out),
        }
        print(f"DONE {fname}: {n_in:,} rows, {n_dup_dropped:,} held out of combined as cross-file dups", flush=True)

    if combined_writer is not None:
        combined_writer.close()

    if sample_frames:
        sample = pd.concat(sample_frames, ignore_index=True)
        sample_path = sample_dir / "reviews_sample.csv"
        sample.to_csv(sample_path, index=False)
        stats["sample_csv"] = str(sample_path)
        stats["sample_rows"] = len(sample)

    print("Computing reviewer-level aggregates from combined parquet...", flush=True)
    reviewer_path = OUT_DIR / "reviewers.parquet"
    compute_reviewer_table(combined_path, reviewer_path)
    stats["reviewers_output"] = str(reviewer_path)
    stats["elapsed_sec"] = round(time.time() - t0, 2)

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"Track 1 complete in {stats['elapsed_sec']}s. Manifest: {manifest_path}", flush=True)


def compute_reviewer_table(reviews_path: Path, out_path: Path) -> None:
    """Delegate to DuckDB aggregation; do not load the combined parquet into pandas."""
    from aggregate_reviewers import COMBINED, OUT_PARQUET, aggregate

    if Path(reviews_path).resolve() != COMBINED.resolve():
        raise ValueError(f"Expected combined parquet {COMBINED}, got {reviews_path}")
    if Path(out_path).resolve() != OUT_PARQUET.resolve():
        raise ValueError(f"Expected reviewer parquet {OUT_PARQUET}, got {out_path}")
    aggregate()


if __name__ == "__main__":
    main()
