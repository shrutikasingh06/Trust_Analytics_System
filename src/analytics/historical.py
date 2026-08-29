"""Exact-identifier historical lookup via DuckDB on existing parquet tables.

Never loads the 21.9M-row review table into pandas. Never treats matches as live.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.config import HISTORICAL_REVIEW_LIMIT, ROOT
from src.product_ids import looks_like_catalog_id
from src.schemas import Product, Review

log = logging.getLogger("trust_analytics.historical")

PRODUCT_FEATURES = ROOT / "dataset" / "cleaned" / "features" / "product_features.parquet"
REVIEW_FEATURES = ROOT / "dataset" / "cleaned" / "features" / "review_features.parquet"
REVIEWER_FEATURES = ROOT / "dataset" / "cleaned" / "features" / "reviewer_features.parquet"
CATEGORY_DIR = ROOT / "dataset" / "cleaned" / "behavior_eda" / "reviews_by_category"
COMBINED_REVIEWS = ROOT / "dataset" / "cleaned" / "behavior_eda" / "reviews_combined_dedup.parquet"

_CACHE_MAX = 24
_cache: OrderedDict[str, "HistoricalMatch | None"] = OrderedDict()
_cache_lock = threading.Lock()


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def historical_tables_available() -> bool:
    return PRODUCT_FEATURES.is_file()


@dataclass
class HistoricalMatch:
    product_id: str
    n_available: int
    n_sampled: int
    product_stats: dict = field(default_factory=dict)
    review_stats: dict = field(default_factory=dict)
    reviews: list[Review] = field(default_factory=list)
    product: Product | None = None


def lookup_historical(product_id: str, platform: str, url: str) -> HistoricalMatch | None:
    """Exact ASIN lookup. Amazon URLs only — never map other storefronts onto this corpus."""
    if platform != "amazon":
        return None
    if not looks_like_catalog_id(product_id) or not historical_tables_available():
        return None
    key = product_id.strip().upper()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            cached = _cache[key]
            return cached
    match = _load_match(key, platform, url)
    with _cache_lock:
        _cache[key] = match
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return match


def clear_historical_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _connect():
    con = duckdb.connect(database=":memory:")
    con.execute("SET memory_limit='2GB'")
    con.execute("SET preserve_insertion_order=false")
    return con


def _load_match(asin: str, platform: str, url: str) -> HistoricalMatch | None:
    con = _connect()
    try:
        prow = con.execute(
            f"SELECT * FROM read_parquet('{_sql_path(PRODUCT_FEATURES)}') WHERE asin = ?",
            [asin],
        ).fetchone()
        if prow is None:
            return None
        cols = [c[0] for c in con.description]
        stats = {cols[i]: prow[i] for i in range(len(cols))}
        n_available = int(stats.get("review_count") or 0)
        if n_available <= 0:
            return None
        review_stats = _review_feature_aggregates(con, asin)
        limit = min(HISTORICAL_REVIEW_LIMIT, n_available)
        reviews = _load_reviews(con, asin, stats.get("category"), platform, limit)
        if not reviews:
            return None
        product = Product(
            platform=platform,
            product_id=asin,
            url=url,
            title=None,
            brand=None,
            category=stats.get("category") if isinstance(stats.get("category"), str) else None,
            rating=float(stats["avg_rating"]) if stats.get("avg_rating") is not None else None,
            rating_count=n_available,
            review_count=n_available,
        )
        return HistoricalMatch(
            product_id=asin,
            n_available=n_available,
            n_sampled=len(reviews),
            product_stats=_jsonable(stats),
            review_stats=_jsonable(review_stats),
            reviews=reviews,
            product=product,
        )
    except Exception:
        log.warning("Historical dataset lookup failed for an exact product id")
        return None
    finally:
        con.close()


def _review_feature_aggregates(con, asin: str) -> dict:
    if not REVIEW_FEATURES.is_file():
        return {}
    row = con.execute(
        f"""
        SELECT
            COUNT(*) AS n,
            median(rating) AS median_rating,
            avg(review_length_chars) AS avg_review_length_chars,
            avg(helpful_votes) AS avg_helpful_votes,
            avg(CASE WHEN is_exact_duplicate_text THEN 1.0 ELSE 0.0 END) AS exact_duplicate_share,
            sum(CASE WHEN text_available THEN 1 ELSE 0 END) AS n_with_text
        FROM read_parquet('{_sql_path(REVIEW_FEATURES)}')
        WHERE asin = ?
        """,
        [asin],
    ).fetchone()
    if not row:
        return {}
    keys = [c[0] for c in con.description]
    return {keys[i]: row[i] for i in range(len(keys))}


def _load_reviews(con, asin: str, category: str | None, platform: str, limit: int) -> list[Review]:
    path = None
    if isinstance(category, str) and category:
        candidate = CATEGORY_DIR / f"{category}.parquet"
        if candidate.is_file():
            path = candidate
    if path is None and COMBINED_REVIEWS.is_file():
        path = COMBINED_REVIEWS
    if path is None:
        return _reviews_from_features(con, asin, platform, limit)
    rows = con.execute(
        f"""
        SELECT mongo_id, reviewerID, asin, reviewerName, overall, reviewText, summary,
               helpful_votes, unixReviewTime, review_datetime_utc
        FROM read_parquet('{_sql_path(path)}')
        WHERE asin = ?
        LIMIT {int(limit)}
        """,
        [asin],
    ).fetchall()
    out: list[Review] = []
    for row in rows:
        text = row[5] if isinstance(row[5], str) and row[5].strip() else None
        title = row[6] if isinstance(row[6], str) and row[6].strip() else None
        out.append(
            Review(
                platform=platform,
                product_id=asin,
                review_id=str(row[0]) if row[0] is not None else None,
                reviewer_id=str(row[1]) if row[1] is not None else None,
                reviewer_name=row[3] if isinstance(row[3], str) else None,
                rating=float(row[4]) if row[4] is not None else None,
                title=title,
                text=text,
                helpful_votes=int(row[7]) if row[7] is not None else None,
                review_date=_parse_ts(row[9], row[8]),
                verified_purchase=None,
            )
        )
    return out


def _reviews_from_features(con, asin: str, platform: str, limit: int) -> list[Review]:
    if not REVIEW_FEATURES.is_file():
        return []
    rows = con.execute(
        f"""
        SELECT review_id, reviewerID, rating, helpful_votes, review_datetime_utc
        FROM read_parquet('{_sql_path(REVIEW_FEATURES)}')
        WHERE asin = ?
        LIMIT {int(limit)}
        """,
        [asin],
    ).fetchall()
    out = []
    for row in rows:
        out.append(
            Review(
                platform=platform,
                product_id=asin,
                review_id=str(row[0]) if row[0] is not None else None,
                reviewer_id=str(row[1]) if row[1] is not None else None,
                rating=float(row[2]) if row[2] is not None else None,
                helpful_votes=int(row[3]) if row[3] is not None else None,
                review_date=_parse_ts(row[4], None),
            )
        )
    return out


def _parse_ts(value, unix) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if unix is None:
        return None
    try:
        return datetime.fromtimestamp(int(unix), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _jsonable(obj: dict) -> dict:
    out = {}
    for k, v in (obj or {}).items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float):
            out[k] = round(v, 6)
        else:
            out[k] = v
    return out
