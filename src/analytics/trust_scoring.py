"""
Transparent product trust / review-risk scoring from LIVE features only.

This is not a trained “fake review detector”. It does not use Track 2 heuristic labels
or star-rating polarity (`class`) as ground truth. Scores are bounded, documented
adjustments from a neutral prior of 50.
"""
from __future__ import annotations

from src.schemas import ReviewFlags, RiskFactor, TrustAnalysis


def score_from_features(features: dict) -> TrustAnalysis:
    n = int(features.get("n_reviews") or 0)
    notes = [
        "Scores use only reviews returned for this URL. Historical training parquet was not used as this product’s reviews.",
        "Language is risk-pattern based. This is not a claim that any review is fake.",
    ]
    if n == 0:
        return TrustAnalysis(
            product_trust_score=None,
            review_risk_band="Unknown",
            n_reviews_used=0,
            data_mode="LIVE",
            historical_training_data_used_as_product_reviews=False,
            metrics={},
            contributing_factors=[],
            rating_distribution=features.get("rating_distribution") or {},
            review_flags=[],
            notes=notes + ["No live reviews were available, so no trust score was computed."],
        )

    score = 50.0
    factors: list[RiskFactor] = []

    # Sample size (log scale, capped)
    sample_boost = min(15.0, math_log_boost(n))
    score += sample_boost
    factors.append(
        RiskFactor(
            direction="positive" if sample_boost else "neutral",
            label="Live sample size",
            detail=f"{n} live review(s) used. Larger samples increase confidence in the pattern summary.",
            feature="n_reviews",
            value=n,
        )
    )

    diversity = features.get("reviewer_diversity")
    if diversity is not None:
        delta = 12.0 * (diversity - 0.5)
        score += delta
        factors.append(
            RiskFactor(
                direction="positive" if delta >= 0 else "negative",
                label="Reviewer diversity",
                detail=f"Unique reviewers / reviews = {diversity:.2f} (live sample only).",
                feature="reviewer_diversity",
                value=round(diversity, 4),
            )
        )
    elif "reviewer_id" in str(features.get("features_skipped")):
        factors.append(
            RiskFactor(
                direction="neutral",
                label="Reviewer diversity unavailable",
                detail="reviewer_id was not provided by the data source, so concentration was not scored.",
                feature="reviewer_diversity",
                value=None,
            )
        )

    dup = features.get("duplicate_text_ratio")
    if dup is not None:
        delta = -20.0 * dup
        score += delta
        factors.append(
            RiskFactor(
                direction="negative" if dup > 0 else "positive",
                label="Exact duplicate-text ratio",
                detail=f"Share of reviews whose text hash appears more than once: {dup:.2f}.",
                feature="duplicate_text_ratio",
                value=round(dup, 4),
            )
        )

    near = features.get("near_duplicate_pairs") or 0
    if features.get("n_with_text"):
        if near:
            score -= min(10.0, 2.0 * near)
            factors.append(
                RiskFactor(
                    direction="negative",
                    label="Near-duplicate text pairs",
                    detail=f"{near} pair(s) with sequence similarity ≥ 0.92 (live sample).",
                    feature="near_duplicate_pairs",
                    value=near,
                )
            )
        else:
            factors.append(
                RiskFactor(
                    direction="positive",
                    label="Near-duplicate text pairs",
                    detail="No near-duplicate pairs at similarity ≥ 0.92 in the live sample.",
                    feature="near_duplicate_pairs",
                    value=0,
                )
            )

    five = features.get("five_star_ratio")
    n_rated = features.get("n_with_rating") or 0
    if five is not None and n_rated >= 8:
        if five >= 0.85:
            delta = -12.0 * min(1.0, (five - 0.85) / 0.15)
            score += delta
            factors.append(
                RiskFactor(
                    direction="negative",
                    label="High concentration of 5-star reviews",
                    detail=f"{five:.0%} of rated live reviews are 5-star. Common on many stores; treated as a pattern, not proof of abuse.",
                    feature="five_star_ratio",
                    value=round(five, 4),
                )
            )
        else:
            factors.append(
                RiskFactor(
                    direction="positive",
                    label="5-star concentration not extreme",
                    detail=f"5-star share is {five:.0%} in the live rated sample.",
                    feature="five_star_ratio",
                    value=round(five, 4),
                )
            )

    entropy = features.get("rating_entropy")
    if entropy is not None and n_rated >= 8:
        max_ent = math_ln(5)
        norm = entropy / max_ent if max_ent else 0
        if norm < 0.35:
            score -= 8.0
            factors.append(
                RiskFactor(
                    direction="negative",
                    label="Low rating-distribution entropy",
                    detail=f"Entropy {entropy:.2f} (normalized {norm:.2f}). Ratings are concentrated on few stars.",
                    feature="rating_entropy",
                    value=round(entropy, 4),
                )
            )
        else:
            factors.append(
                RiskFactor(
                    direction="positive",
                    label="Rating distribution not collapsed",
                    detail=f"Entropy {entropy:.2f} (normalized {norm:.2f}) over 1–5 stars.",
                    feature="rating_entropy",
                    value=round(entropy, 4),
                )
            )

    avg_len = features.get("avg_review_length_chars")
    if avg_len is not None and n >= 5:
        if avg_len < 25:
            score -= 6.0
            factors.append(
                RiskFactor(
                    direction="negative",
                    label="Very short average review text",
                    detail=f"Mean length {avg_len:.0f} characters.",
                    feature="avg_review_length_chars",
                    value=round(avg_len, 1),
                )
            )
        elif avg_len >= 80:
            score += 4.0
            factors.append(
                RiskFactor(
                    direction="positive",
                    label="Review text length",
                    detail=f"Mean length {avg_len:.0f} characters.",
                    feature="avg_review_length_chars",
                    value=round(avg_len, 1),
                )
            )

    burst = features.get("reviews_per_active_day")
    span = features.get("review_span_days")
    if burst is not None and span is not None and n >= 8:
        if span < 2 and burst >= 5:
            score -= 8.0
            factors.append(
                RiskFactor(
                    direction="negative",
                    label="High-risk temporal concentration",
                    detail=f"{n} reviews spanning {span:.1f} days ({burst:.1f} per inclusive active day). Potentially coordinated timing — not proof.",
                    feature="reviews_per_active_day",
                    value=round(burst, 3),
                )
            )

    max_one = features.get("max_reviews_one_reviewer")
    if max_one is not None and n >= 5 and max_one / n >= 0.4:
        score -= 7.0
        factors.append(
            RiskFactor(
                direction="negative",
                label="Review concentration on few reviewers",
                detail=f"One reviewer accounts for {max_one} of {n} live reviews.",
                feature="max_reviews_one_reviewer",
                value=max_one,
            )
        )

    score_i = int(round(min(100.0, max(0.0, score))))

    flags = [ReviewFlags(**f) for f in features.get("review_flags") or []]
    high = sum(1 for f in flags if f.risk_band == "High")
    med = sum(1 for f in flags if f.risk_band == "Medium")
    if n == 0:
        band = "Unknown"
    elif high / n >= 0.25 or (high + med) / n >= 0.5:
        band = "High"
    elif (high + med) / n >= 0.2:
        band = "Medium"
    else:
        band = "Low"

    metrics = {
        "n_reviews": n,
        "avg_rating": _r(features.get("avg_rating")),
        "rating_std": _r(features.get("rating_std")),
        "five_star_ratio": _r(features.get("five_star_ratio")),
        "duplicate_text_ratio": _r(features.get("duplicate_text_ratio")),
        "reviewer_diversity": _r(features.get("reviewer_diversity")),
        "avg_review_length_chars": _r(features.get("avg_review_length_chars")),
        "near_duplicate_pairs": features.get("near_duplicate_pairs"),
        "rating_entropy": _r(features.get("rating_entropy")),
    }

    return TrustAnalysis(
        product_trust_score=score_i,
        review_risk_band=band,
        n_reviews_used=n,
        data_mode="LIVE",
        historical_training_data_used_as_product_reviews=False,
        metrics=metrics,
        contributing_factors=factors,
        rating_distribution=features.get("rating_distribution") or {},
        review_flags=flags,
        notes=notes,
    )


def math_log_boost(n: int) -> float:
    import math

    return 8.0 * math.log10(n + 1)


def math_ln(x: float) -> float:
    import math

    return math.log(x)


def _r(v):
    if v is None:
        return None
    if isinstance(v, float):
        return round(v, 4)
    return v
