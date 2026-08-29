"""
Platform-independent trust analytics engine for LIVE normalized reviews.

Reuses `compute_live_features` (formulas aligned with FEATURE_CATALOG.md).
Does not load historical parquet. Does not use candidate_label or BFR.
Does not treat sentiment or star polarity as fake-review ground truth.

Product trust is a weighted blend of available components (see WEIGHTS).
Unavailable signals are omitted and remaining weights are renormalized.
This is a risk assessment, not a claim that any review is fake.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from statistics import mean, pstdev

from src.analytics.live_features import NEAR_DUP_RATIO, compute_live_features
from src.analytics.models import (
    ComponentScore,
    ExplanationItem,
    ReviewAnalysis,
    ReviewerAnalysis,
    TrustEngineResult,
)
from src.analytics.sentiment import rating_polarity, score_text_sentiment
from src.analytics.trust_scoring import score_from_features
from src.schemas import Review, ReviewFlags, RiskFactor, TrustAnalysis

# Documented prior weights. Sum to 1.0. Missing components are dropped and the rest renormalized.
WEIGHTS = {
    "review_quality": 0.20,
    "rating_consistency": 0.20,
    "reviewer_reliability": 0.20,
    "suspicious_review_risk": 0.25,
    "review_diversity_activity": 0.15,
}

WEIGHTING_NOTES = [
    "Weights are a documented research prior, not a fitted model. No validation accuracy is claimed.",
    "If a component cannot be computed from live fields, it is omitted and remaining weights are renormalized.",
    "Review risk score is behavioral. 0 is low risk and 100 is high risk. It is not verified fraud ground truth.",
    "Sentiment is lexicon polarity of text. Star rating polarity is separate. Neither is a fake-review label.",
    "Reviewer metrics use only reviewers observed in THIS live sample. Historical reviewer_features.parquet is not joined.",
]


def analyze_live_reviews(reviews: list[Review]) -> tuple[TrustEngineResult, TrustAnalysis]:
    features = compute_live_features(reviews)
    compat = score_from_features(features)
    if not reviews:
        empty = TrustEngineResult(
            product_trust_score=None,
            trust_level=None,
            suspicion_risk=None,
            suspicion_level="Unknown",
            n_reviews=0,
            missing_signals=["no live reviews"],
            weighting_notes=WEIGHTING_NOTES,
        )
        return empty, compat

    review_rows = _review_analyses(reviews, features)
    reviewer_rows = _reviewer_analyses(reviews)
    suspicions = [r.suspicion_risk for r in review_rows]
    mean_susp = int(round(mean(suspicions))) if suspicions else None
    susp_level = _band(mean_susp) if mean_susp is not None else "Unknown"

    components = _components(features, mean_susp, reviewer_rows)
    trust_score, applied = _blend(components)
    trust_level = _trust_level(trust_score)

    explanations = _explanations(features, review_rows, components, applied)
    sentiment_labels = [r.sentiment_label for r in review_rows if r.sentiment_label]
    sc = Counter(sentiment_labels)

    engine = TrustEngineResult(
        product_trust_score=trust_score,
        trust_level=trust_level,
        suspicion_risk=mean_susp,
        suspicion_level=susp_level if isinstance(susp_level, str) else "Unknown",
        n_reviews=len(reviews),
        review_analyses=review_rows,
        reviewer_analyses=reviewer_rows,
        review_summary={
            "n_reviews": len(reviews),
            "n_with_text": features.get("n_with_text"),
            "n_with_rating": features.get("n_with_rating"),
            "avg_rating": _r(features.get("avg_rating")),
            "avg_review_length_chars": _r(features.get("avg_review_length_chars")),
            "rating_distribution": features.get("rating_distribution"),
            "duplicate_text_ratio": _r(features.get("duplicate_text_ratio")),
        },
        sentiment_summary={
            "method": "lexicon",
            "distinct_from_star_rating": True,
            "distinct_from_suspicion_risk": True,
            "n_scored": sum(1 for r in review_rows if r.sentiment_label is not None),
            "n_unscored_missing_text": sum(1 for r in review_rows if not r.text_present),
            "counts": dict(sc),
        },
        suspicious_review_summary={
            "mean_suspicion_risk": mean_susp,
            "level": susp_level,
            "n_high": sum(1 for r in review_rows if r.risk_level == "High"),
            "n_medium": sum(1 for r in review_rows if r.risk_level == "Medium"),
            "n_low": sum(1 for r in review_rows if r.risk_level == "Low"),
            "disclaimer": "Risk assessment only. Not a claim that reviews are fake.",
        },
        reviewer_summary=_reviewer_summary(reviewer_rows, features),
        breakdown=components,
        explanations=explanations,
        weighting_notes=WEIGHTING_NOTES,
        missing_signals=list(features.get("features_skipped") or []),
    )

    compat.product_trust_score = trust_score
    compat.review_risk_band = susp_level if susp_level in {"Low", "Medium", "High", "Unknown"} else "Unknown"
    compat.contributing_factors = [
        RiskFactor(
            direction=e.direction,
            label=e.feature,
            detail=e.interpretation,
            feature=e.feature,
            value=e.observed_value,
        )
        for e in explanations
    ]
    compat.review_flags = [
        ReviewFlags(
            review_id=r.review_id,
            rating=r.rating,
            flags=r.reasons,
            risk_band=r.risk_level,
        )
        for r in review_rows
    ]
    return engine, compat


def _review_analyses(reviews: list[Review], features: dict) -> list[ReviewAnalysis]:
    texts = [(r.text or "").strip() or None for r in reviews]
    hashes = [hashlib.sha256(t.lower().encode("utf-8")).hexdigest() if t else None for t in texts]
    hash_counts = Counter(h for h in hashes if h is not None)
    n_rated = features.get("n_with_rating") or 0
    product_avg = features.get("avg_rating")
    helpful_vals = [r.helpful_votes for r in reviews if r.helpful_votes is not None]
    helpful_mean = mean(helpful_vals) if helpful_vals else None
    helpful_std = pstdev(helpful_vals) if len(helpful_vals) >= 2 else None

    near_idx: set[int] = set()
    for i, a in enumerate(texts):
        if not a:
            continue
        for j, b in enumerate(texts):
            if j <= i or not b or a == b:
                continue
            if SequenceMatcher(None, a.lower(), b.lower()).ratio() >= NEAR_DUP_RATIO:
                near_idx.add(i)
                near_idx.add(j)

    rows: list[ReviewAnalysis] = []
    for i, r in enumerate(reviews):
        t = texts[i]
        sent = score_text_sentiment(t)
        reasons: list[str] = []
        expl: list[ExplanationItem] = []
        risk = 0

        length = len(t) if t else None
        words = len(t.split()) if t else None

        if t and hash_counts[hashes[i]] > 1:
            reasons.append("unusually repetitive text")
            risk += 35
            expl.append(_e("exact_duplicate_text", hash_counts[hashes[i]], "Same text hash appears more than once in this live sample.", "negative", 35))
        if i in near_idx:
            reasons.append("near-duplicate text")
            risk += 20
            expl.append(_e("near_duplicate_text", 1, "Sequence similarity ≥ 0.92 with another live review.", "negative", 20))
        if t is not None and length is not None and length < 20:
            reasons.append("very short review text")
            risk += 15
            expl.append(_e("review_length_chars", length, "Text shorter than 20 characters.", "negative", 15))
        if r.rating is not None and product_avg is not None and n_rated >= 5 and abs(r.rating - product_avg) >= 2.5:
            reasons.append("abnormal rating behavior")
            risk += 15
            expl.append(_e("product_rating_deviation", round(r.rating - product_avg, 3), "Rating differs from live product mean by ≥ 2.5 stars.", "negative", 15))
        if (
            r.helpful_votes is not None
            and helpful_mean is not None
            and helpful_std is not None
            and helpful_std > 0
            and len(helpful_vals) >= 5
            and r.helpful_votes > helpful_mean + 2 * helpful_std
        ):
            reasons.append("excessive helpfulness anomaly")
            risk += 10
            expl.append(_e("helpful_votes", r.helpful_votes, "Helpful votes exceed live-sample mean + 2 SD. Exposure-biased; not proof of manipulation.", "negative", 10))

        risk = int(min(100, risk))
        rows.append(
            ReviewAnalysis(
                review_id=r.review_id,
                rating=r.rating,
                rating_polarity=rating_polarity(r.rating),
                text_present=bool(t),
                review_length_chars=length,
                review_length_words=words,
                helpful_votes=r.helpful_votes,
                sentiment_label=sent["label"],
                sentiment_score=sent["score"],
                suspicion_risk=risk,
                risk_level=_band(risk),
                reasons=reasons,
                explanations=expl,
            )
        )
    return rows


def _reviewer_analyses(reviews: list[Review]) -> list[ReviewerAnalysis]:
    by_id: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        if r.reviewer_id:
            by_id[r.reviewer_id].append(r)
    out: list[ReviewerAnalysis] = []
    for rid, rows in by_id.items():
        ratings = [x.rating for x in rows if x.rating is not None]
        products = {x.product_id for x in rows if x.product_id}
        dates = [x.review_date for x in rows if x.review_date]
        missing = []
        if not ratings:
            missing.append("rating")
        if len(products) == 0:
            missing.append("product_id")
        if len(dates) < 2:
            missing.append("review_date span (need ≥2 timestamps)")
        five = (sum(1 for x in ratings if x == 5) / len(ratings)) if ratings else None
        span = None
        rate = None
        if len(dates) >= 2:
            span = (max(dates) - min(dates)).total_seconds() / 86400.0
            rate = len(rows) / (span + 1.0)
        elif len(dates) == 1:
            span = 0.0
            rate = float(len(rows))
        reasons = []
        trust = 70
        if five is not None and five >= 0.9 and len(ratings) >= 3:
            reasons.append("rating concentration (almost all 5-star in this sample)")
            trust -= 15
        if rate is not None and span is not None and span < 1 and len(rows) >= 3:
            reasons.append("unusual activity pattern (several reviews in under one day in this sample)")
            trust -= 20
        if len(rows) >= 3:
            reasons.append("product/reviewer concentration in this listing sample")
            trust -= 10
        trust = int(min(100, max(0, trust)))
        band = _band(100 - trust) if reasons else "Low"
        out.append(
            ReviewerAnalysis(
                reviewer_id=rid,
                review_count=len(rows),
                unique_products=len(products) if products else None,
                avg_rating=round(mean(ratings), 4) if ratings else None,
                rating_std=round(pstdev(ratings), 4) if len(ratings) >= 2 else None,
                five_star_ratio=round(five, 4) if five is not None else None,
                review_span_days=round(span, 4) if span is not None else None,
                reviews_per_active_day=round(rate, 4) if rate is not None else None,
                reviewer_trust_score=trust,
                risk_level=band if band in {"Low", "Medium", "High"} else "Unknown",
                reasons=reasons,
                missing_signals=missing,
            )
        )
    return out


def _reviewer_summary(rows: list[ReviewerAnalysis], features: dict) -> dict:
    if not rows:
        return {
            "available": False,
            "reason": "No reviewer_id on live reviews; reviewer behavior was not invented.",
            "n_reviewers": 0,
        }
    trusts = [r.reviewer_trust_score for r in rows if r.reviewer_trust_score is not None]
    return {
        "available": True,
        "n_reviewers": len(rows),
        "mean_reviewer_trust_score": int(round(mean(trusts))) if trusts else None,
        "reviewer_diversity": _r(features.get("reviewer_diversity")),
        "max_reviews_one_reviewer": features.get("max_reviews_one_reviewer"),
        "scope": "Computed only from reviewers present in this live product sample.",
    }


def _components(features: dict, mean_susp: int | None, reviewers: list[ReviewerAnalysis]) -> list[ComponentScore]:
    n = features.get("n_reviews") or 0
    n_text = features.get("n_with_text") or 0
    avg_len = features.get("avg_review_length_chars")
    quality = None
    q_note = "Need review text."
    if n and n_text:
        coverage = n_text / n
        length_part = min(1.0, (avg_len or 0) / 200.0) if avg_len is not None else 0.0
        quality = 100.0 * (0.55 * coverage + 0.45 * length_part)
        q_note = "How complete and detailed the written reviews are."

    consistency = None
    c_note = "Need ratings."
    n_rated = features.get("n_with_rating") or 0
    if n_rated >= 2:
        ent = features.get("rating_entropy") or 0.0
        ent_n = ent / math.log(5) if math.log(5) else 0
        five = features.get("five_star_ratio") or 0.0
        # High entropy and not extreme 5-star pile-up raise consistency.
        consistency = 100.0 * (0.65 * min(1.0, ent_n / 0.8) + 0.35 * (1.0 - min(1.0, max(0.0, five - 0.5) / 0.5)))
        c_note = "How mixed or one-sided the star ratings are."

    reliability = None
    r_note = "Need reviewer_id."
    div = features.get("reviewer_diversity")
    if div is not None:
        reliability = 100.0 * div
        r_note = "How many different reviewers appear in this live sample."
    elif reviewers:
        reliability = mean([x.reviewer_trust_score for x in reviewers if x.reviewer_trust_score is not None])
        r_note = "Reviewer patterns observed in this live sample only."

    susp = None
    s_note = "Need live reviews."
    if mean_susp is not None:
        susp = 100.0 - mean_susp
        s_note = "Lower suspicion-risk indicators support a higher trust score."

    activity = None
    a_note = "Need review count."
    if n:
        sample = min(1.0, math.log10(n + 1) / math.log10(51))
        activity = 100.0 * sample
        a_note = "How many live reviews were available for this product."
        span = features.get("review_span_days")
        burst = features.get("reviews_per_active_day")
        if span is not None and burst is not None and n >= 8 and span < 2 and burst >= 5:
            activity = max(0.0, activity - 25)
            a_note += " Reduced because many reviews arrived in a very short period."

    return [
        ComponentScore(name="review_quality", weight=WEIGHTS["review_quality"], applied_weight=None, score=_r(quality), available=quality is not None, note=q_note),
        ComponentScore(name="rating_consistency", weight=WEIGHTS["rating_consistency"], applied_weight=None, score=_r(consistency), available=consistency is not None, note=c_note),
        ComponentScore(name="reviewer_reliability", weight=WEIGHTS["reviewer_reliability"], applied_weight=None, score=_r(reliability), available=reliability is not None, note=r_note),
        ComponentScore(name="suspicious_review_risk", weight=WEIGHTS["suspicious_review_risk"], applied_weight=None, score=_r(susp), available=susp is not None, note=s_note),
        ComponentScore(name="review_diversity_activity", weight=WEIGHTS["review_diversity_activity"], applied_weight=None, score=_r(activity), available=activity is not None, note=a_note),
    ]


def _blend(components: list[ComponentScore]) -> tuple[int | None, list[ComponentScore]]:
    avail = [c for c in components if c.available and c.score is not None]
    if not avail:
        return None, components
    wsum = sum(c.weight for c in avail)
    if wsum <= 0:
        return None, components
    total = 0.0
    out = []
    for c in components:
        if c.available and c.score is not None:
            aw = c.weight / wsum
            total += aw * c.score
            out.append(c.model_copy(update={"applied_weight": round(aw, 4)}))
        else:
            out.append(c.model_copy(update={"applied_weight": 0.0}))
    return int(round(min(100.0, max(0.0, total)))), out


_COMPONENT_TITLES = {
    "review_quality": "Review quality",
    "rating_consistency": "Rating consistency",
    "reviewer_reliability": "Reviewer reliability",
    "suspicious_review_risk": "Behavioral consistency",
    "review_diversity_activity": "Review activity",
}


def _component_sentence(name: str, score: float) -> str:
    if name == "review_quality":
        if score >= 55:
            return "Written reviews were detailed enough to support the trust score."
        if score < 45:
            return "Limited or very short review text reduced the trust score."
        return "Review text quality had a modest effect on the trust score."
    if name == "rating_consistency":
        if score >= 55:
            return "Ratings are relatively consistent across the analyzed reviews."
        if score < 45:
            return "A one-sided or unusually tight rating mix reduced the trust score."
        return "Star-rating mix had a modest effect on the trust score."
    if name == "reviewer_reliability":
        if score >= 55:
            return "Reviews came from a varied set of reviewers, which supported trust."
        if score < 45:
            return "Reviewer patterns in this sample reduced the trust score."
        return "Reviewer patterns had a modest effect on the trust score."
    if name == "suspicious_review_risk":
        if score >= 55:
            return "Few behavioral risk indicators were found, which supported the trust score."
        if score < 45:
            return "Behavioral risk indicators reduced the trust score."
        return "Behavioral risk indicators had a modest effect on the trust score."
    if name == "review_diversity_activity":
        if score >= 55:
            return "The number of live reviews was large enough to support the trust score."
        if score < 45:
            return "A small or tightly clustered review sample reduced the trust score."
        return "Review volume had a modest effect on the trust score."
    return "This signal contributed to the product trust score."


def _explanations(features: dict, reviews: list[ReviewAnalysis], components: list[ComponentScore], applied: list[ComponentScore]) -> list[ExplanationItem]:
    items: list[ExplanationItem] = []
    for c in applied:
        if c.applied_weight and c.score is not None:
            items.append(
                ExplanationItem(
                    feature=_COMPONENT_TITLES.get(c.name, c.name.replace("_", " ").capitalize()),
                    observed_value=round(c.score) if isinstance(c.score, float) else c.score,
                    interpretation=_component_sentence(c.name, c.score),
                    direction="positive" if c.score >= 55 else ("negative" if c.score < 45 else "neutral"),
                    contribution=round((c.applied_weight or 0) * (c.score - 50), 2),
                    component=c.name,
                )
            )
    if features.get("duplicate_text_ratio") is not None:
        d = features["duplicate_text_ratio"]
        if d > 0:
            interp = "Several reviews share the same wording, which is a risk indicator."
            direction = "negative"
        else:
            interp = "Review wording in this sample is not repeated."
            direction = "positive"
        items.append(_e("Repeated review text", round(d, 4), interp, direction, None, "suspicious_review_risk"))
    if features.get("reviewer_diversity") is not None:
        div = features["reviewer_diversity"]
        interp = (
            "Different reviewers wrote these reviews, which supports reliability."
            if div >= 0.7
            else "Fewer distinct reviewers appear in this sample than expected."
        )
        items.append(_e("Reviewer variety", round(div, 4), interp, "positive" if div >= 0.7 else "negative", None, "reviewer_reliability"))
    else:
        items.append(_e(
            "Reviewer evidence",
            "Unavailable",
            "Reviewer-level evidence was unavailable from this source.",
            "neutral",
            None,
            "reviewer_reliability",
        ))
    short = sum(1 for r in reviews if "very short review text" in (r.reasons or []))
    high = sum(1 for r in reviews if r.risk_level == "High")
    if short:
        items.append(_e(
            "Short-text risk indicators",
            short,
            "Several reviews show short-text risk indicators.",
            "negative",
            None,
            "suspicious_review_risk",
        ))
    items.append(_e(
        "High suspicion-risk reviews",
        high,
        "Several reviews show elevated suspicion-risk indicators." if high else "Few reviews in this sample show high suspicion-risk indicators.",
        "negative" if high else "positive",
        None,
        "suspicious_review_risk",
    ))
    labels = [r.sentiment_label for r in reviews if r.sentiment_label]
    if labels:
        top, n_top = Counter(labels).most_common(1)[0]
        if top == "positive" and n_top / len(labels) >= 0.5:
            items.append(_e("Review sentiment", n_top, "Review sentiment is predominantly positive.", "neutral", None, None))
        elif top == "negative" and n_top / len(labels) >= 0.5:
            items.append(_e("Review sentiment", n_top, "Review sentiment is predominantly negative.", "neutral", None, None))
        else:
            items.append(_e("Review sentiment", n_top, "Review sentiment is mixed across the analyzed reviews.", "neutral", None, None))
    return items


def _e(feature, value, interpretation, direction, contribution=None, component=None) -> ExplanationItem:
    return ExplanationItem(
        feature=feature,
        observed_value=value,
        interpretation=interpretation,
        direction=direction,
        contribution=contribution,
        component=component,
    )


def _band(risk: int) -> Literal["Low", "Medium", "High"]:
    if risk >= 67:
        return "High"
    if risk >= 34:
        return "Medium"
    return "Low"


def _trust_level(score: int | None) -> Literal["High", "Moderate", "Low"] | None:
    if score is None:
        return None
    if score >= 70:
        return "High"
    if score >= 45:
        return "Moderate"
    return "Low"


def _r(v):
    if v is None:
        return None
    if isinstance(v, float):
        return round(v, 4)
    return v
