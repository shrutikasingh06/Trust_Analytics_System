"""
Lexicon sentiment for live review text.

This is NOT fake-review detection and is NOT the same as star-rating polarity.
Missing text → sentiment is None (never invented).
"""
from __future__ import annotations

import re

# Compact English polarity lexicon. Not a trained model; scores are directional only.
_POS = {
    "excellent", "amazing", "great", "good", "love", "loved", "awesome", "perfect",
    "happy", "best", "wonderful", "fantastic", "quality", "recommend", "worth",
    "smooth", "easy", "reliable", "satisfied", "nice", "beautiful", "fast",
    "comfortable", "useful", "impressive", "pleased", "enjoy",
}
_NEG = {
    "bad", "poor", "terrible", "awful", "hate", "worst", "broken", "waste",
    "useless", "disappointing", "disappointed", "defect", "defective", "slow",
    "fake", "fraud", "scam", "refund", "return", "cheap", "flimsy", "late",
    "horrible", "issue", "problem", "fail", "failed", "never", "wrong",
}

_TOKEN = re.compile(r"[a-z']+")


def score_text_sentiment(text: str | None) -> dict:
    if not text or not str(text).strip():
        return {
            "label": None,
            "score": None,
            "positive_hits": 0,
            "negative_hits": 0,
            "method": "lexicon",
            "note": "No review text; sentiment not computed.",
        }
    tokens = _TOKEN.findall(text.lower())
    pos = sum(1 for t in tokens if t in _POS)
    neg = sum(1 for t in tokens if t in _NEG)
    if pos == 0 and neg == 0:
        return {
            "label": "neutral",
            "score": 0.0,
            "positive_hits": 0,
            "negative_hits": 0,
            "method": "lexicon",
            "note": "No lexicon hits; treated as neutral, not as trust.",
        }
    raw = (pos - neg) / (pos + neg)
    if raw > 0.2:
        label = "positive"
    elif raw < -0.2:
        label = "negative"
    else:
        label = "mixed"
    return {
        "label": label,
        "score": round(raw, 4),
        "positive_hits": pos,
        "negative_hits": neg,
        "method": "lexicon",
        "note": "Lexicon polarity of text. Distinct from star rating and from suspicion risk.",
    }


def rating_polarity(rating: float | None) -> str | None:
    """Star-rating polarity (FEATURE_CATALOG `class`), not sentiment and not trust."""
    if rating is None:
        return None
    if rating >= 4:
        return "positive"
    if rating <= 2:
        return "negative"
    return "neutral"
