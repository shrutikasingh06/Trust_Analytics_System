"""
Live-review feature engineering.

Formulas align with dataset/cleaned/features/FEATURE_CATALOG.md (review / reviewer / product
aggregates) but run only on the reviews returned for THIS product URL.

Historical parquet tables (review_features.parquet, etc.) are NEVER joined or substituted.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from statistics import pstdev

from src.schemas import Review

NEAR_DUP_RATIO = 0.92


def _text(r: Review) -> str | None:
    t = (r.text or "").strip()
    return t if t else None


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def compute_live_features(reviews: list[Review]) -> dict:
    n = len(reviews)
    texts = [_text(r) for r in reviews]
    ratings = [r.rating for r in reviews if r.rating is not None]
    n_rated = len(ratings)
    lengths = [len(t) for t in texts if t is not None]
    words = [len(t.split()) for t in texts if t is not None]
    helpful = [r.helpful_votes for r in reviews if r.helpful_votes is not None]
    verified = [r.verified_purchase for r in reviews if r.verified_purchase is not None]
    dates = [r.review_date for r in reviews if r.review_date is not None]
    reviewer_ids = [r.reviewer_id for r in reviews if r.reviewer_id]
    titles = [r.title for r in reviews if r.title]

    dist = {str(k): 0 for k in range(1, 6)}
    for rt in ratings:
        star = int(round(rt))
        if 1 <= star <= 5:
            dist[str(star)] += 1

    product_avg = (sum(ratings) / n_rated) if n_rated else None
    rating_std = pstdev(ratings) if n_rated >= 2 else None

    five_star_ratio = (dist["5"] / n_rated) if n_rated else None
    one_star_ratio = (dist["1"] / n_rated) if n_rated else None
    extreme_ratio = ((dist["1"] + dist["5"]) / n_rated) if n_rated else None

    entropy = None
    if n_rated:
        probs = [dist[str(k)] / n_rated for k in range(1, 6) if dist[str(k)]]
        entropy = -sum(p * math.log(p) for p in probs)

    hash_counts: Counter[str] = Counter()
    for t in texts:
        if t:
            hash_counts[_text_hash(t)] += 1
    n_with_text = sum(1 for t in texts if t)
    exact_dup_reviews = sum(c for c in hash_counts.values() if c > 1)
    duplicate_ratio = (exact_dup_reviews / n_with_text) if n_with_text else None

    near_dup_pairs = 0
    unique_texts = [t for t in texts if t]
    for i, a in enumerate(unique_texts):
        for b in unique_texts[i + 1 :]:
            if a == b:
                continue
            if SequenceMatcher(None, a.lower(), b.lower()).ratio() >= NEAR_DUP_RATIO:
                near_dup_pairs += 1

    unique_reviewers = len(set(reviewer_ids))
    reviewer_diversity = (unique_reviewers / n) if n and reviewer_ids else None
    if n and not reviewer_ids:
        reviewer_diversity = None

    reviews_per_reviewer = None
    max_reviews_one_reviewer = None
    if reviewer_ids:
        rc = Counter(reviewer_ids)
        reviews_per_reviewer = n / len(rc)
        max_reviews_one_reviewer = max(rc.values())

    span_days = None
    reviews_per_active_day = None
    if len(dates) >= 2:
        span = (max(dates) - min(dates)).total_seconds() / 86400.0
        span_days = span
        inclusive = span + 1.0
        reviews_per_active_day = n / inclusive if inclusive > 0 else None
    elif len(dates) == 1:
        span_days = 0.0
        reviews_per_active_day = float(n)

    deviations = None
    if product_avg is not None and n_rated:
        deviations = [abs(r - product_avg) for r in ratings]
    mean_abs_dev = (sum(deviations) / len(deviations)) if deviations else None

    by_reviewer_avg: dict[str, list[float]] = defaultdict(list)
    for r in reviews:
        if r.reviewer_id and r.rating is not None:
            by_reviewer_avg[r.reviewer_id].append(r.rating)

    review_flags: list[dict] = []
    for r, t in zip(reviews, texts):
        flags: list[str] = []
        if t is not None and len(t) < 20:
            flags.append("very_short_text")
        if t is not None and hash_counts[_text_hash(t)] > 1:
            flags.append("exact_duplicate_text")
        if r.rating is not None and product_avg is not None and n_rated >= 5:
            if abs(r.rating - product_avg) >= 2.5:
                flags.append("large_rating_deviation")
        if r.verified_purchase is False:
            flags.append("unverified_purchase_when_field_present")
        n_flags = len(flags)
        if n_flags >= 3:
            band = "High"
        elif n_flags == 2:
            band = "Medium"
        elif n_flags == 1:
            band = "Medium"
        else:
            band = "Low"
        review_flags.append(
            {
                "review_id": r.review_id,
                "rating": r.rating,
                "flags": flags,
                "risk_band": band,
            }
        )

    return {
        "n_reviews": n,
        "n_with_rating": n_rated,
        "n_with_text": n_with_text,
        "n_with_helpful_votes": len(helpful),
        "n_with_verified_purchase": len(verified),
        "n_with_review_date": len(dates),
        "n_with_reviewer_id": len(reviewer_ids),
        "n_with_title": len(titles),
        "avg_rating": product_avg,
        "rating_std": rating_std,
        "five_star_ratio": five_star_ratio,
        "one_star_ratio": one_star_ratio,
        "extreme_rating_ratio": extreme_ratio,
        "rating_entropy": entropy,
        "rating_distribution": {k: int(v) for k, v in dist.items()},
        "avg_review_length_chars": (sum(lengths) / len(lengths)) if lengths else None,
        "avg_review_length_words": (sum(words) / len(words)) if words else None,
        "median_review_length_chars": (sorted(lengths)[len(lengths) // 2] if lengths else None),
        "avg_helpful_votes": (sum(helpful) / len(helpful)) if helpful else None,
        "verified_purchase_share": (sum(1 for v in verified if v) / len(verified)) if verified else None,
        "exact_duplicate_review_count": exact_dup_reviews,
        "duplicate_text_ratio": duplicate_ratio,
        "near_duplicate_pairs": near_dup_pairs,
        "unique_reviewers": unique_reviewers if reviewer_ids else None,
        "reviewer_diversity": reviewer_diversity,
        "reviews_per_reviewer": reviews_per_reviewer,
        "max_reviews_one_reviewer": max_reviews_one_reviewer,
        "review_span_days": span_days,
        "reviews_per_active_day": reviews_per_active_day,
        "mean_abs_rating_deviation": mean_abs_dev,
        "review_flags": review_flags,
        "features_used": _used(locals(), n, n_rated, n_with_text, reviewer_ids, helpful, verified, dates),
        "features_skipped": _skipped(n, n_rated, n_with_text, reviewer_ids, helpful, verified, dates),
    }


def _used(loc, n, n_rated, n_with_text, reviewer_ids, helpful, verified, dates) -> list[str]:
    used = ["n_reviews"]
    if n_rated:
        used += ["avg_rating", "rating_distribution"]
        if n_rated >= 2:
            used.append("rating_std")
        used += ["five_star_ratio", "rating_entropy"]
    if n_with_text:
        used += ["avg_review_length_chars", "duplicate_text_ratio"]
    if reviewer_ids:
        used.append("reviewer_diversity")
    if helpful:
        used.append("avg_helpful_votes")
    if verified:
        used.append("verified_purchase_share")
    if dates:
        used.append("review_span_days")
    return used


def _skipped(n, n_rated, n_with_text, reviewer_ids, helpful, verified, dates) -> list[str]:
    skipped = []
    if not n_rated:
        skipped.append("rating-based metrics (no ratings on live reviews)")
    if n_rated < 2:
        skipped.append("rating_std (need ≥2 ratings)")
    if not n_with_text:
        skipped.append("text/duplicate metrics (no review text)")
    if not reviewer_ids:
        skipped.append("reviewer diversity / concentration (no reviewer_id)")
    if not helpful:
        skipped.append("helpful_votes metrics (field unavailable)")
    if not verified:
        skipped.append("verified_purchase metrics (field unavailable)")
    if not dates:
        skipped.append("temporal burst metrics (no review_date)")
    return skipped
