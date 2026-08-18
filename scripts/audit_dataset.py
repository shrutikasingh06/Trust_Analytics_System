"""
Read-only data-quality audit of raw CSVs in dataset/csv_files/.
Does not modify original files. Writes JSON + markdown under dataset/cleaned/.
"""
from __future__ import annotations

import ast
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\Trust_Analytics_System")
RAW_DIR = ROOT / "dataset" / "csv_files"
OUT_DIR = ROOT / "dataset" / "cleaned"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CHUNKSIZE = 200_000
MAX_UNIQUE_STORE = 12_000_000
BFR_PARSE_BUDGET = 200_000

LOW_CARD = {"overall", "class", "label", "category"}
BFR_KEYS = ["CS", "MNR", "RB", "RC", "PR", "NR", "FR", "RSP", "AW", "RD", "RL", "ER", "PC"]
NA_TOKENS = {"", "nan", "none", "null", "NaN", "None", "NULL"}


def fmt_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{x:.2f} {unit}"
        x /= 1024
    return f"{x:.2f} GB"


def to_py(obj):
    if isinstance(obj, dict):
        return {str(k): to_py(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_py(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def miss_mask(s: pd.Series) -> pd.Series:
    t = s.fillna("").astype(str).str.strip()
    return t.eq("") | t.str.lower().isin(["nan", "none", "null"])


def hash_series(s: pd.Series) -> np.ndarray:
    return pd.util.hash_pandas_object(s.fillna("").astype(str), index=False).to_numpy()


def hash_frame(df: pd.DataFrame) -> np.ndarray:
    return pd.util.hash_pandas_object(df.fillna("").astype(str), index=False).to_numpy()


def update_set_with_dups(seen: set, hashes: np.ndarray, overflow_flag: dict, key: str) -> int:
    extra = 0
    if overflow_flag.get(key):
        return extra
    for h in hashes.tolist():
        if h in seen:
            extra += 1
        else:
            seen.add(h)
            if len(seen) > MAX_UNIQUE_STORE:
                overflow_flag[key] = True
                break
    return extra


def parse_helpful_vector(s: pd.Series) -> dict:
    t = s.fillna("").astype(str).str.strip()
    miss = miss_mask(s)
    # [n, n] or [n.0, n.0]
    pat = t.str.match(r"^\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\]$", na=False)
    extracted = t.str.extract(r"^\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]$", expand=True)
    a = pd.to_numeric(extracted[0], errors="coerce")
    b = pd.to_numeric(extracted[1], errors="coerce")
    parsed = a.notna() & b.notna()
    out = {
        "missing": int(miss.sum()),
        "ok": int(parsed.sum()),
        "unparseable": int((~miss & ~parsed).sum()),
        "negative": int(((a < 0) | (b < 0)).fillna(False).sum()),
        "helpful_gt_total": int((a > b).fillna(False).sum()),
        "non_integer": int((((a % 1) != 0) | ((b % 1) != 0)).fillna(False).sum()),
        "n_parsed": int(parsed.sum()),
        "sum_a": float(a[parsed].sum()) if parsed.any() else 0.0,
        "sum_b": float(b[parsed].sum()) if parsed.any() else 0.0,
        "regex_match": int(pat.sum()),
    }
    return out


def parse_bfr_values(values, budget_left: int) -> tuple[dict, int]:
    status = Counter()
    keys = Counter()
    numeric = {k: {"min": None, "max": None, "n": 0, "sum": 0.0} for k in BFR_KEYS}
    parsed_n = 0
    for val in values:
        if budget_left <= 0:
            if val is None or str(val).strip() == "" or str(val).strip().lower() in NA_TOKENS:
                status["missing"] += 1
            else:
                status["present_unparsed"] += 1
            continue
        if val is None or (isinstance(val, float) and math.isnan(val)):
            status["missing"] += 1
            continue
        text = str(val).strip()
        if text == "" or text.lower() in NA_TOKENS:
            status["missing"] += 1
            continue
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            status["unparseable"] += 1
            budget_left -= 1
            continue
        if not isinstance(parsed, dict):
            status["wrong_type"] += 1
            budget_left -= 1
            continue
        status["ok"] += 1
        parsed_n += 1
        budget_left -= 1
        keys.update(parsed.keys())
        for k in BFR_KEYS:
            if k not in parsed:
                continue
            try:
                v = float(parsed[k])
            except Exception:
                continue
            st = numeric[k]
            st["n"] += 1
            st["sum"] += v
            st["min"] = v if st["min"] is None else min(st["min"], v)
            st["max"] = v if st["max"] is None else max(st["max"], v)
    return {
        "status": dict(status),
        "keys": dict(keys),
        "numeric": numeric,
        "parsed_n": parsed_n,
    }, budget_left


def count_newlines(path: Path) -> int:
    n = 0
    with path.open("rb") as fh:
        while True:
            buf = fh.read(16 * 1024 * 1024)
            if not buf:
                break
            n += buf.count(b"\n")
    return n


def audit_file(path: Path) -> dict:
    t0 = time.time()
    size = path.stat().st_size
    print(f"\n=== Auditing {path.name} ({fmt_bytes(size)}) ===", flush=True)

    columns = pd.read_csv(path, nrows=0).columns.tolist()
    n_cols = len(columns)

    missing = Counter()
    value_counts = {c: Counter() for c in columns if c in LOW_CARD}
    numeric_stats = {
        c: {"min": None, "max": None, "sum": 0.0, "sumsq": 0.0, "n": 0, "non_numeric": 0}
        for c in columns
        if c in {"overall", "unixReviewTime", "class", "label"}
    }

    review_len = {
        "n": 0,
        "min": None,
        "max": None,
        "sum": 0,
        "empty": 0,
        "very_short_lt5": 0,
        "very_long_gt5000": 0,
        "whitespace_only": 0,
    }
    summary_empty = 0
    summary_n = 0
    encoding_errors = 0
    empty_rows = 0

    helpful_agg = Counter()
    helpful_sum_a = 0.0
    helpful_sum_b = 0.0
    helpful_n = 0

    bfr_status = Counter()
    bfr_keys = Counter()
    bfr_numeric = {k: {"min": None, "max": None, "n": 0, "sum": 0.0} for k in BFR_KEYS}
    bfr_parsed_n = 0
    bfr_budget = BFR_PARSE_BUDGET

    class_by_overall = Counter()
    label_by_overall = Counter()
    label_by_class = Counter()
    class_counts = Counter()
    label_counts = Counter()

    id_like_oid = 0
    id_plain = 0
    overall_not_1_to_5 = 0
    overall_non_intish = 0
    unix_invalid_extra = 0
    reviewtime_unparseable = 0
    unix_vs_reviewtime_mismatch = 0
    unix_minmax = [None, None]

    unique_sets = {
        "reviewerID": set(),
        "asin": set(),
        "_id": set(),
        "row_hash": set(),
        "id_triple": set(),
        "id_text": set(),
    }
    overflow = {k: False for k in unique_sets}
    duplicate_row_hash = 0
    duplicate_id = 0
    duplicate_triple = 0
    duplicate_text_pair = 0
    triple_hashes = set()
    triple_overflow = False

    sample_head = []
    n_rows = 0
    chunks = 0
    parse_warn = 0

    reader = pd.read_csv(
        path,
        chunksize=CHUNKSIZE,
        dtype=str,
        keep_default_na=False,
        na_values=[],
        encoding="utf-8",
        encoding_errors="replace",
        engine="c",
        on_bad_lines="warn",
    )

    for chunk in reader:
        chunks += 1
        n_rows += len(chunk)
        if chunks == 1:
            sample_head = chunk.head(2).to_dict(orient="records")

        miss_cols = {}
        for c in columns:
            if c not in chunk.columns:
                continue
            m = miss_mask(chunk[c])
            miss_cols[c] = m
            missing[c] += int(m.sum())

        if miss_cols:
            er = None
            for m in miss_cols.values():
                er = m if er is None else (er & m)
            empty_rows += int(er.sum())

        for c in value_counts:
            if c not in chunk.columns:
                continue
            vc = chunk[c].astype(str).str.strip().value_counts()
            value_counts[c].update({str(k): int(v) for k, v in vc.items()})

        for c, st in numeric_stats.items():
            if c not in chunk.columns:
                continue
            raw = chunk[c].astype(str).str.strip()
            miss = miss_cols[c]
            nums = pd.to_numeric(raw.where(~miss), errors="coerce")
            st["non_numeric"] += int((~miss & nums.isna()).sum())
            valid = nums.dropna()
            if len(valid):
                vmin, vmax = float(valid.min()), float(valid.max())
                st["min"] = vmin if st["min"] is None else min(st["min"], vmin)
                st["max"] = vmax if st["max"] is None else max(st["max"], vmax)
                arr = valid.astype("float64")
                st["sum"] += float(arr.sum())
                st["sumsq"] += float((arr ** 2).sum())
                st["n"] += int(len(valid))

        if "overall" in chunk.columns:
            nums = pd.to_numeric(chunk["overall"], errors="coerce")
            valid = nums.dropna()
            if len(valid):
                overall_not_1_to_5 += int(((valid < 1) | (valid > 5)).sum())
                overall_non_intish += int((valid != valid.round()).sum())

        if "unixReviewTime" in chunk.columns:
            nums = pd.to_numeric(chunk["unixReviewTime"], errors="coerce")
            miss = miss_cols["unixReviewTime"]
            unix_invalid_extra += int((~miss & nums.isna()).sum())
            valid = nums.dropna()
            if len(valid):
                vmin, vmax = float(valid.min()), float(valid.max())
                unix_minmax[0] = vmin if unix_minmax[0] is None else min(unix_minmax[0], vmin)
                unix_minmax[1] = vmax if unix_minmax[1] is None else max(unix_minmax[1], vmax)
                # before 1995-01-01 or after 2020-01-01 UTC
                unix_invalid_extra += int(((valid < 788918400) | (valid > 1577836800)).sum())

        if "reviewTime" in chunk.columns:
            rt = chunk["reviewTime"].astype(str).str.strip()
            parsed_rt = pd.to_datetime(rt, errors="coerce")
            miss_rt = miss_cols["reviewTime"]
            reviewtime_unparseable += int((~miss_rt & parsed_rt.isna()).sum())
            if "unixReviewTime" in chunk.columns:
                un = pd.to_numeric(chunk["unixReviewTime"], errors="coerce")
                both = parsed_rt.notna() & un.notna()
                if both.any():
                    unix_dates = pd.to_datetime(un[both], unit="s", utc=True).dt.tz_convert(None).dt.normalize()
                    rt_dates = pd.to_datetime(parsed_rt[both]).dt.normalize()
                    unix_vs_reviewtime_mismatch += int((unix_dates.values != rt_dates.values).sum())

        if "reviewText" in chunk.columns:
            txt = chunk["reviewText"].astype(str)
            miss = miss_cols["reviewText"]
            stripped = txt.str.strip()
            whitespace_only = (~miss) & stripped.eq("")
            # miss_mask already treats whitespace-only as missing via strip
            lens = txt.str.len()
            valid_lens = lens[~miss]
            review_len["empty"] += int(miss.sum())
            review_len["whitespace_only"] += int(whitespace_only.sum())
            if len(valid_lens):
                review_len["n"] += int(len(valid_lens))
                review_len["sum"] += int(valid_lens.sum())
                mn, mx = int(valid_lens.min()), int(valid_lens.max())
                review_len["min"] = mn if review_len["min"] is None else min(review_len["min"], mn)
                review_len["max"] = mx if review_len["max"] is None else max(review_len["max"], mx)
                review_len["very_short_lt5"] += int((valid_lens < 5).sum())
                review_len["very_long_gt5000"] += int((valid_lens > 5000).sum())
            encoding_errors += int(txt.str.contains("\ufffd", na=False).sum())

        if "summary" in chunk.columns:
            miss = miss_cols["summary"]
            summary_empty += int(miss.sum())
            summary_n += int((~miss).sum())

        if "helpful" in chunk.columns:
            h = parse_helpful_vector(chunk["helpful"])
            helpful_agg["missing"] += h["missing"]
            helpful_agg["ok"] += h["ok"]
            helpful_agg["unparseable"] += h["unparseable"]
            helpful_agg["negative"] += h["negative"]
            helpful_agg["helpful_gt_total"] += h["helpful_gt_total"]
            helpful_agg["non_integer"] += h["non_integer"]
            helpful_n += h["n_parsed"]
            helpful_sum_a += h["sum_a"]
            helpful_sum_b += h["sum_b"]

        if "BehaviouralFeatureResult" in chunk.columns:
            parsed, bfr_budget = parse_bfr_values(chunk["BehaviouralFeatureResult"].tolist(), bfr_budget)
            bfr_status.update(parsed["status"])
            bfr_keys.update(parsed["keys"])
            bfr_parsed_n += parsed["parsed_n"]
            for k, st in parsed["numeric"].items():
                dst = bfr_numeric[k]
                if st["n"]:
                    dst["n"] += st["n"]
                    dst["sum"] += st["sum"]
                    dst["min"] = st["min"] if dst["min"] is None else min(dst["min"], st["min"])
                    dst["max"] = st["max"] if dst["max"] is None else max(dst["max"], st["max"])

        if "class" in chunk.columns:
            cl = chunk["class"].astype(str).str.strip()
            class_counts.update(cl.value_counts().to_dict())
            if "overall" in chunk.columns:
                tmp = chunk.assign(_o=chunk["overall"].astype(str).str.strip(), _c=cl)
                class_by_overall.update(tmp.groupby(["_o", "_c"], sort=False).size().to_dict())
        if "label" in chunk.columns:
            lb = chunk["label"].astype(str).str.strip()
            label_counts.update(lb.value_counts().to_dict())
            if "overall" in chunk.columns:
                tmp = chunk.assign(_o=chunk["overall"].astype(str).str.strip(), _l=lb)
                label_by_overall.update(tmp.groupby(["_o", "_l"], sort=False).size().to_dict())
            if "class" in chunk.columns:
                tmp = chunk.assign(_c=chunk["class"].astype(str).str.strip(), _l=lb)
                label_by_class.update(tmp.groupby(["_c", "_l"], sort=False).size().to_dict())

        if "_id" in chunk.columns:
            ids = chunk["_id"].astype(str)
            oid = ids.str.contains(r"\$oid", na=False)
            id_like_oid += int(oid.sum())
            id_plain += int((~oid & ~miss_cols["_id"]).sum())

        if "reviewerID" in chunk.columns and not overflow["reviewerID"]:
            unique_sets["reviewerID"].update(hash_series(chunk["reviewerID"]).tolist())
            if len(unique_sets["reviewerID"]) > MAX_UNIQUE_STORE:
                overflow["reviewerID"] = True
        if "asin" in chunk.columns and not overflow["asin"]:
            unique_sets["asin"].update(hash_series(chunk["asin"]).tolist())
            if len(unique_sets["asin"]) > MAX_UNIQUE_STORE:
                overflow["asin"] = True
        if "_id" in chunk.columns:
            h = hash_series(chunk["_id"])
            duplicate_id += update_set_with_dups(unique_sets["_id"], h, overflow, "_id")

        if {"reviewerID", "asin", "unixReviewTime"}.issubset(chunk.columns):
            h = hash_frame(chunk[["reviewerID", "asin", "unixReviewTime"]])
            duplicate_triple += update_set_with_dups(unique_sets["id_triple"], h, overflow, "id_triple")
            if not triple_overflow:
                before = len(triple_hashes)
                triple_hashes.update(h.tolist())
                if len(triple_hashes) > MAX_UNIQUE_STORE:
                    triple_overflow = True
                    triple_hashes = set()
                _ = before
        if {"reviewerID", "asin", "reviewText"}.issubset(chunk.columns):
            h = hash_frame(chunk[["reviewerID", "asin", "reviewText"]])
            duplicate_text_pair += update_set_with_dups(unique_sets["id_text"], h, overflow, "id_text")
        key_cols = [c for c in ["reviewerID", "asin", "unixReviewTime", "reviewText", "overall"] if c in chunk.columns]
        if key_cols:
            h = hash_frame(chunk[key_cols])
            duplicate_row_hash += update_set_with_dups(unique_sets["row_hash"], h, overflow, "row_hash")

        if chunks % 3 == 0:
            print(f"  {path.name}: {n_rows:,} rows | {chunks} chunks | {time.time()-t0:.1f}s", flush=True)

    newline_count = count_newlines(path)
    # data rows ≈ newlines - 1 if trailing newline; malformed ≈ extra lines vs parsed rows
    expected_data_lines = max(newline_count - 1, 0)
    line_vs_rows_delta = expected_data_lines - n_rows

    num_out = {}
    for c, st in numeric_stats.items():
        out = dict(st)
        if st["n"]:
            mean = st["sum"] / st["n"]
            var = max(st["sumsq"] / st["n"] - mean * mean, 0.0)
            out["mean"] = mean
            out["std"] = math.sqrt(var)
        num_out[c] = out

    missing_pct = {
        c: {"missing": int(missing[c]), "pct": round(100.0 * missing[c] / n_rows, 4) if n_rows else None}
        for c in columns
    }

    def pair_counter(ctr: Counter, limit=80):
        items = ctr.most_common(limit)
        out = {}
        for k, v in items:
            if isinstance(k, tuple) and len(k) == 2:
                out[f"{k[0]} | {k[1]}"] = int(v)
            else:
                out[str(k)] = int(v)
        return out

    review_out = dict(review_len)
    if review_len["n"]:
        review_out["mean"] = review_len["sum"] / review_len["n"]

    unix_readable = None
    if unix_minmax[0] is not None:
        try:
            unix_readable = {
                "min_iso": datetime.fromtimestamp(unix_minmax[0], tz=timezone.utc).isoformat(),
                "max_iso": datetime.fromtimestamp(unix_minmax[1], tz=timezone.utc).isoformat(),
            }
        except Exception:
            unix_readable = {"min": unix_minmax[0], "max": unix_minmax[1]}

    result = {
        "file_name": path.name,
        "file_size_bytes": size,
        "file_size_human": fmt_bytes(size),
        "n_rows": n_rows,
        "n_columns": n_cols,
        "columns": columns,
        "newline_count": newline_count,
        "expected_data_lines_if_one_record_per_line": expected_data_lines,
        "line_count_minus_parsed_rows": line_vs_rows_delta,
        "missing": missing_pct,
        "empty_rows_all_fields": empty_rows,
        "numeric_stats": num_out,
        "low_cardinality_value_counts": {c: dict(value_counts[c].most_common(40)) for c in value_counts},
        "class_counts": dict(class_counts),
        "label_counts": dict(label_counts),
        "class_by_overall": pair_counter(class_by_overall),
        "label_by_overall": pair_counter(label_by_overall),
        "label_by_class": pair_counter(label_by_class),
        "review_text": review_out,
        "summary_empty": summary_empty,
        "summary_n": summary_n,
        "helpful_status": dict(helpful_agg),
        "helpful_means": {
            "n_parsed": helpful_n,
            "mean_helpful_votes": (helpful_sum_a / helpful_n) if helpful_n else None,
            "mean_total_votes": (helpful_sum_b / helpful_n) if helpful_n else None,
        },
        "bfr_status": dict(bfr_status),
        "bfr_keys_observed": dict(bfr_keys),
        "bfr_numeric": bfr_numeric,
        "bfr_fully_parsed_n": bfr_parsed_n,
        "unique_counts": {k: len(v) for k, v in unique_sets.items()},
        "unique_overflow": overflow,
        "duplicate_extra_row_hash_hits": duplicate_row_hash,
        "duplicate_extra_id_hits": duplicate_id,
        "duplicate_extra_triple_hits": duplicate_triple,
        "duplicate_extra_reviewer_asin_text_hits": duplicate_text_pair,
        "id_format": {"mongo_oid_like": id_like_oid, "plain_or_other": id_plain},
        "overall_not_1_to_5": overall_not_1_to_5,
        "overall_non_integer": overall_non_intish,
        "unix_minmax": unix_minmax,
        "unix_readable": unix_readable,
        "unix_implausible_or_non_numeric_extra": unix_invalid_extra,
        "reviewtime_unparseable": reviewtime_unparseable,
        "unix_vs_reviewtime_date_mismatches": unix_vs_reviewtime_mismatch,
        "encoding_replacement_char_rows": encoding_errors,
        "sample_head": to_py(sample_head),
        "elapsed_sec": round(time.time() - t0, 2),
        "triple_hashes": triple_hashes if not triple_overflow else set(),
        "triple_overflow": triple_overflow,
        "n_chunks": chunks,
        "parse_warnings_note": "C engine on_bad_lines=warn; compare newline delta for malformed multiline text",
    }
    print(
        f"DONE {path.name}: rows={n_rows:,} newlines={newline_count:,} "
        f"class={dict(class_counts)} label={dict(label_counts)} {time.time()-t0:.1f}s",
        flush=True,
    )
    return result


def analyze_class_meaning(file_results: list[dict]) -> dict:
    out = {}
    for r in file_results:
        cbo = r.get("class_by_overall") or {}
        if not cbo:
            continue
        mapping = defaultdict(Counter)
        for k, v in cbo.items():
            if " | " not in k:
                continue
            o, c = k.split(" | ", 1)
            mapping[o][c] += v
        deterministic = True
        rules = {}
        for o, ctr in mapping.items():
            nonempty = {k2: n for k2, n in ctr.items() if str(k2).lower() not in {"nan", "", "none", "null"}}
            if len(nonempty) > 1:
                deterministic = False
            rules[o] = dict(ctr)
        out[r["file_name"]] = {
            "appears_deterministic_from_overall": deterministic,
            "overall_to_class": rules,
        }
    return out


def overlap_analysis(file_results: list[dict]) -> dict:
    names = [r["file_name"] for r in file_results]
    sets = {r["file_name"]: r.get("triple_hashes") or set() for r in file_results}
    overflow = {r["file_name"]: r.get("triple_overflow") for r in file_results}
    pairwise = {}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            sa, sb = sets[a], sets[b]
            if overflow[a] or overflow[b] or not sa or not sb:
                pairwise[f"{a} ∩ {b}"] = {
                    "status": "not_computed_or_overflow",
                    "overflow_a": overflow[a],
                    "overflow_b": overflow[b],
                    "len_a": len(sa),
                    "len_b": len(sb),
                }
                continue
            inter = len(sa & sb)
            pairwise[f"{a} ∩ {b}"] = {
                "intersection_triples": inter,
                "pct_of_a": round(100.0 * inter / len(sa), 4) if sa else None,
                "pct_of_b": round(100.0 * inter / len(sb), 4) if sb else None,
                "len_a": len(sa),
                "len_b": len(sb),
            }
    return pairwise


def schema_comparison(file_results: list[dict]) -> dict:
    schemas = {r["file_name"]: r["columns"] for r in file_results}
    all_cols = sorted({c for cols in schemas.values() for c in cols})
    matrix = {c: {fn: (c in cols) for fn, cols in schemas.items()} for c in all_cols}
    return {"all_columns": all_cols, "per_file": schemas, "presence_matrix": matrix}


def write_report(file_results: list[dict], extra: dict, dest: Path) -> None:
    lines = []
    lines.append("# Data Quality Report — Trust Analytics System")
    lines.append("")
    lines.append("**Stage:** audit only (no cleaning applied). Original CSVs were not modified.")
    lines.append(f"**Generated:** {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## 1. Dataset overview")
    lines.append("")
    lines.append(
        "Raw inputs are Amazon-style product review CSVs. Six files are category-specific "
        "and include a `class` column. `part.csv` and `separate.csv` use different schemas "
        "and add `BehaviouralFeatureResult` and `label`."
    )
    lines.append("")
    total_rows = sum(r["n_rows"] for r in file_results)
    total_bytes = sum(r["file_size_bytes"] for r in file_results)
    lines.append(f"- Files analyzed: **{len(file_results)}**")
    lines.append(f"- Combined file size: **{fmt_bytes(total_bytes)}**")
    lines.append(f"- Combined parsed rows (sum across files, not de-duplicated): **{total_rows:,}**")
    lines.append("")
    lines.append("## 2–4. Files analyzed, sizes, row/column counts")
    lines.append("")
    lines.append("| File | Size | Parsed rows | Newlines | Line−row delta | Columns | Column names |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for r in file_results:
        cols = ", ".join(f"`{c}`" for c in r["columns"])
        lines.append(
            f"| `{r['file_name']}` | {r['file_size_human']} | {r['n_rows']:,} | {r['newline_count']:,} | "
            f"{r['line_count_minus_parsed_rows']:,} | {r['n_columns']} | {cols} |"
        )
    lines.append("")
    lines.append(
        "A large positive line−row delta usually means review text contains embedded newlines "
        "(multiline quoted fields), not necessarily malformed records. A large negative delta "
        "would be more concerning (parser merged/skipped lines)."
    )
    lines.append("")

    lines.append("## 5. Missing-value analysis")
    lines.append("")
    for r in file_results:
        lines.append(f"### `{r['file_name']}`")
        lines.append("")
        lines.append("| Column | Missing | % |")
        lines.append("|---|---:|---:|")
        for c, info in r["missing"].items():
            lines.append(f"| `{c}` | {info['missing']:,} | {info['pct']} |")
        lines.append(f"- Fully empty rows: **{r['empty_rows_all_fields']:,}**")
        lines.append("")

    lines.append("## 6. Duplicate analysis")
    lines.append("")
    lines.append(
        "Hashes of `_id`, `(reviewerID, asin, unixReviewTime)`, `(reviewerID, asin, reviewText)`, "
        "and `(reviewerID, asin, unixReviewTime, reviewText, overall)`. "
        "`duplicate_extra_*_hits` = extra occurrences after the first. Hash collisions are possible but rare."
    )
    lines.append("")
    lines.append(
        "| File | Unique reviewerID | Unique asin | Extra `_id` | Extra triple | Extra same-text | Extra row-key | Overflow |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for r in file_results:
        u = r["unique_counts"]
        ov = {k: v for k, v in r["unique_overflow"].items() if v}
        lines.append(
            f"| `{r['file_name']}` | {u.get('reviewerID', 0):,} | {u.get('asin', 0):,} | "
            f"{r['duplicate_extra_id_hits']:,} | {r['duplicate_extra_triple_hits']:,} | "
            f"{r['duplicate_extra_reviewer_asin_text_hits']:,} | {r['duplicate_extra_row_hash_hits']:,} | `{ov}` |"
        )
    lines.append("")

    lines.append("## 7. Data-type analysis")
    lines.append("")
    lines.append("Values were read as strings then coerced. Observed types:")
    lines.append("")
    lines.append("- `_id`: string, often Mongo `{' $oid': ... }`")
    lines.append("- `reviewerID`, `asin`, `reviewerName`, `reviewText`, `summary`, `reviewTime`, `category`: strings")
    lines.append("- `helpful`: serialized 2-list `[helpful_votes, total_votes]`")
    lines.append("- `overall`: numeric stars (expected 1–5)")
    lines.append("- `unixReviewTime`: Unix seconds")
    lines.append("- `class` / `label`: 0/1, sometimes written as `0.0`/`1.0`")
    lines.append("- `BehaviouralFeatureResult`: serialized dict of numeric metrics")
    lines.append("")
    for r in file_results:
        ns = r.get("numeric_stats") or {}
        lines.append(f"### `{r['file_name']}`")
        for c, st in ns.items():
            lines.append(
                f"- `{c}`: n={st['n']:,}, min={st['min']}, max={st['max']}, "
                f"mean={st.get('mean')}, std={st.get('std')}, non_numeric={st.get('non_numeric')}"
            )
        lines.append(
            f"- overall outside 1–5: {r['overall_not_1_to_5']:,}; non-integer overall: {r['overall_non_integer']:,}"
        )
        lines.append(
            f"- unix: {r.get('unix_readable')}; reviewTime unparseable: {r['reviewtime_unparseable']:,}; "
            f"unix vs reviewTime date mismatches: {r['unix_vs_reviewtime_date_mismatches']:,}"
        )
        lines.append(f"- helpful: {r['helpful_status']}; means: {r['helpful_means']}")
        lines.append(
            f"- reviewText: {r['review_text']}; replacement-char rows: {r['encoding_replacement_char_rows']:,}; "
            f"summary missing: {r['summary_empty']:,}"
        )
        lines.append(f"- `_id` format: {r['id_format']}")
        lines.append("")

    lines.append("## 8–9. Label analysis and class distribution")
    lines.append("")
    for r in file_results:
        lines.append(f"- `{r['file_name']}` class=`{r['class_counts']}` label=`{r['label_counts']}`")
    lines.append("")
    lines.append("### Is `class` a function of `overall`?")
    lines.append("")
    class_meaning = extra.get("class_meaning") or {}
    for fn, info in class_meaning.items():
        lines.append(f"#### `{fn}`")
        lines.append(f"- Deterministic from `overall`: **{info['appears_deterministic_from_overall']}**")
        for o, ctr in sorted(info["overall_to_class"].items(), key=lambda x: x[0]):
            lines.append(f"  - overall `{o}` → {ctr}")
        lines.append("")
    lines.append("### `label` vs `overall` / `class`")
    lines.append("")
    for r in file_results:
        if r["label_counts"]:
            lines.append(f"#### `{r['file_name']}`")
            lines.append(f"- label_by_overall: `{r['label_by_overall']}`")
            lines.append(f"- label_by_class: `{r['label_by_class']}`")
            lines.append("")
    lines.append("### `BehaviouralFeatureResult`")
    lines.append("")
    for r in file_results:
        if r["bfr_status"]:
            lines.append(f"#### `{r['file_name']}`")
            lines.append(f"- parse status: `{r['bfr_status']}` (fully parsed n={r['bfr_fully_parsed_n']:,})")
            lines.append(f"- keys: `{r['bfr_keys_observed']}`")
            for k, st in r["bfr_numeric"].items():
                if st["n"]:
                    mean = st["sum"] / st["n"]
                    lines.append(f"  - `{k}`: n={st['n']:,}, min={st['min']}, max={st['max']}, mean={mean}")
            lines.append("")
    lines.append("### Interpretation from co-occurrence (no external data dictionary was found)")
    lines.append("")
    lines.append(extra.get("label_narrative", ""))
    lines.append("")

    lines.append("## 10. Dataset inconsistencies")
    lines.append("")
    schemas = extra.get("schemas") or {}
    all_cols = schemas.get("all_columns") or []
    presence = schemas.get("presence_matrix") or {}
    fnames = [r["file_name"] for r in file_results]
    lines.append("| Column | " + " | ".join(f"`{f}`" for f in fnames) + " |")
    lines.append("|" + "---|" * (len(fnames) + 1))
    for c in all_cols:
        row = f"| `{c}` |"
        for f in fnames:
            row += " yes |" if presence.get(c, {}).get(f) else " no |"
        lines.append(row)
    lines.append("")
    for note in extra.get("inconsistency_notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## Potential overlap between files")
    lines.append("")
    lines.append("Measured on hashed `(reviewerID, asin, unixReviewTime)` triples.")
    lines.append("")
    for k, v in (extra.get("overlap") or {}).items():
        lines.append(f"- `{k}`: `{v}`")
    lines.append("")
    lines.append("## 11. Potential data leakage")
    lines.append("")
    for note in extra.get("leakage_notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 12. Recommended cleaning strategy")
    lines.append("")
    for note in extra.get("cleaning_recs") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 13. Final feature recommendations")
    lines.append("")
    for note in extra.get("feature_recs") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 14. Final target recommendation")
    lines.append("")
    lines.append(extra.get("target_rec", ""))
    lines.append("")
    lines.append("## 15. Reasons for excluding any columns")
    lines.append("")
    for note in extra.get("exclude_cols") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 16. Limitations of the dataset")
    lines.append("")
    for note in extra.get("limitations") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## Audit conclusions (A–K)")
    lines.append("")
    lines.append(extra.get("conclusions", ""))
    lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")


def strip_sets(obj):
    if isinstance(obj, dict):
        return {k: strip_sets(v) for k, v in obj.items() if k != "triple_hashes"}
    if isinstance(obj, (list, tuple)):
        return [strip_sets(v) for v in obj]
    if isinstance(obj, set):
        return f"<set n={len(obj)}>"
    return obj


def build_narrative(results, class_meaning) -> str:
    bits = []
    for fn, info in class_meaning.items():
        if info["appears_deterministic_from_overall"]:
            pos, neg = [], []
            for o, ctr in info["overall_to_class"].items():
                nonempty = {k: n for k, n in ctr.items() if str(k).lower() not in {"nan", "", "none", "null"}}
                if not nonempty:
                    continue
                top = max(nonempty, key=nonempty.get)
                try:
                    ov = float(o)
                except Exception:
                    continue
                if top in {"1", "1.0"}:
                    pos.append(ov)
                elif top in {"0", "0.0"}:
                    neg.append(ov)
            bits.append(
                f"In `{fn}`, `class` is a **deterministic function of `overall`** "
                f"(class=1 overalls={sorted(set(pos))}, class=0 overalls={sorted(set(neg))}). "
                "This matches **star-rating polarity / sentiment binarization**, not an independent trust/spam label."
            )
        else:
            bits.append(
                f"In `{fn}`, `class` is **not** a clean 1:1 function of `overall`. See the overall→class table."
            )
    for r in results:
        if r["label_counts"] and r["class_counts"]:
            bits.append(
                f"In `{r['file_name']}`, `label` and `class` co-occur as `{r['label_by_class']}`. "
                "They are **not the same field**."
            )
        elif r["label_counts"]:
            bits.append(
                f"In `{r['file_name']}`, `label` is present without `class`. vs overall: `{r['label_by_overall']}`."
            )
    return "\n\n".join(bits) if bits else "No class/label columns found."


def infer_target_text(results, class_meaning) -> str:
    class_is_rating = all(
        info.get("appears_deterministic_from_overall") for info in class_meaning.values()
    ) and bool(class_meaning)

    label_files = [r for r in results if r["label_counts"]]
    label_independent = False
    label_notes = []
    for r in label_files:
        lbo = r.get("label_by_overall") or {}
        # if every overall maps to mixed labels, more independent of rating
        by_o = defaultdict(Counter)
        for k, v in lbo.items():
            if " | " in k:
                o, lab = k.split(" | ", 1)
                by_o[o][lab] += v
        mixed = 0
        for o, ctr in by_o.items():
            nonempty = {k: n for k, n in ctr.items() if str(k).lower() not in {"nan", "", "none", "null"}}
            if len(nonempty) > 1:
                mixed += 1
        if mixed >= 3:
            label_independent = True
        label_notes.append(f"`{r['file_name']}` label counts={r['label_counts']}, overalls-with-mixed-labels={mixed}")

    parts = []
    if class_is_rating:
        parts.append(
            "**`class` is not a valid trust target.** It is (in the files where it exists) a recode of `overall`. "
            "A model predicting `class` from text/rating would be a **sentiment / polarity** model, not trust analytics. "
            "Reporting accuracy on `class` as 'trust detection' would be scientifically incorrect."
        )
    else:
        parts.append("`class` is not uniformly a recode of `overall`; inspect per-file tables before using it as a target.")

    if not label_files:
        parts.append("No `label` column exists in the category dumps.")
    else:
        parts.append(" ".join(label_notes))
        parts.append(
            "**`label` is a different field from `class`.** Whether it is a reliable ground-truth for "
            "fake/spam/suspicious reviews cannot be proven from column names alone. "
            "If `label` was produced by a rule on `BehaviouralFeatureResult`, then predicting `label` from BFR is circular. "
            "Until provenance is documented, `label` may be used only with that caveat, and BFR should be excluded as features."
        )
        if label_independent:
            parts.append(
                "Observed co-occurrence: `label` is **not** a simple threshold on `overall` "
                "(multiple ratings appear under both label values). That is necessary but not sufficient for a trust label."
            )

    parts.append(
        "**Preferred ML objective given the evidence:** do **not** train a 'trust classifier' on `class`. "
        "If `label` can be documented as independent spam/fake ground truth, review-level classification of `label` "
        "is the only candidate supervised target. Otherwise the defensible project is **unsupervised / descriptive "
        "reviewer-behavior analytics** (aggregates, repetition, helpfulness, rating distributions) plus optional "
        "sentiment analysis clearly labeled as sentiment, not trust."
    )
    return "\n\n".join(parts)


def main():
    files = sorted(RAW_DIR.glob("*.csv"))
    if not files:
        print(f"No CSV files in {RAW_DIR}")
        sys.exit(1)
    print("Files:", [f.name for f in files], flush=True)

    results = []
    for f in files:
        r = audit_file(f)
        results.append(r)
        (OUT_DIR / "audit_partial.json").write_text(
            json.dumps([strip_sets(x) for x in results], indent=2, default=str),
            encoding="utf-8",
        )

    class_meaning = analyze_class_meaning(results)
    overlap = overlap_analysis(results)
    schemas = schema_comparison(results)
    label_narrative = build_narrative(results, class_meaning)
    target_rec = infer_target_text(results, class_meaning)

    extra = {
        "class_meaning": class_meaning,
        "overlap": overlap,
        "schemas": schemas,
        "label_narrative": label_narrative,
        "inconsistency_notes": [
            "Category files share a 12-column schema with `_id` and `class`, without `label` / `BehaviouralFeatureResult`.",
            "`part.csv` has `_id` + `BehaviouralFeatureResult` + `label` and **no** `class`.",
            "`separate.csv` has `class` + `BehaviouralFeatureResult` + `label` and **no** `_id`.",
            "`class` is stored as float-like `0.0`/`1.0`; `label` may be `0`/`1` integers.",
            "`_id` is a Mongo-style dict string, not a model feature.",
            "`helpful` is a serialized list, not two numeric columns.",
            "Do not assume files are disjoint; use overlap hashes.",
        ],
        "leakage_notes": [
            "If target were `class`, `overall` is definitional leakage (or the target *is* the rating).",
            "`summary`/`reviewText` are legitimate for sentiment, not automatically for trust.",
            "`BehaviouralFeatureResult` is derived reviewer/review metrics; using it to predict a label built from the same metrics is circular.",
            "Reviewer aggregates must be computed without the target row / with time-respecting history to avoid leakage.",
            "Never use `label` or `class` as an input feature for a model whose target is that same field.",
        ],
        "cleaning_recs": [
            "Wait for approval before cleaning. Planned (not executed): parse `helpful`; canonicalize timestamps; extract `$oid`; parse BFR for analysis only; flag empty text; deduplicate measured duplicate keys; do not fill NaN labels with 0; do not merge class and label; preserve reviewText; add source_file if concatenating after overlap checks.",
        ],
        "feature_recs": [
            "Raw usable fields: reviewerID, asin, reviewText, summary, overall (as rating), parsed helpful, unixReviewTime, category.",
            "Computable reviewer features: n_reviews, mean/std overall, mean length, helpfulness ratio, time gaps, nunique asin/category, repeated-text flags.",
            "Do not invent trust scores. Do not treat class as trust.",
            "BFR keys need a paper/code definition; until then they are unlabeled derived metrics.",
        ],
        "target_rec": target_rec,
        "exclude_cols": [
            "`_id`: join key only.",
            "`reviewerName`: unstable display name; EDA only.",
            "`reviewTime`: redundant if unix matches; keep one canonical timestamp.",
            "If target is class: exclude overall from features.",
            "If target is label: exclude BFR from features until provenance is proven independent.",
        ],
        "limitations": [
            "No data dictionary in the repo README.",
            "Amazon `class` in this dump behaves like rating polarity, which is not reviewer trust.",
            "Helpfulness is not a trust label.",
            "BFR fully parsed only up to a per-file budget.",
            "Overlap uses hashes with a uniqueness cap.",
        ],
        "conclusions": target_rec + "\n\n" + label_narrative,
    }

    json_path = OUT_DIR / "audit_results.json"
    json_path.write_text(json.dumps([strip_sets(x) for x in results], indent=2, default=str), encoding="utf-8")
    write_report(results, extra, OUT_DIR / "DATA_QUALITY_REPORT.md")
    summary = {
        "files": [
            {
                "file": r["file_name"],
                "rows": r["n_rows"],
                "cols": r["columns"],
                "class_counts": r["class_counts"],
                "label_counts": r["label_counts"],
                "dup_triple": r["duplicate_extra_triple_hits"],
                "dup_row": r["duplicate_extra_row_hash_hits"],
                "elapsed_sec": r["elapsed_sec"],
            }
            for r in results
        ],
        "class_meaning": class_meaning,
        "overlap": overlap,
        "target_rec": target_rec,
    }
    (OUT_DIR / "audit_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("Wrote audit_results.json, audit_summary.json, DATA_QUALITY_REPORT.md", flush=True)


if __name__ == "__main__":
    main()
