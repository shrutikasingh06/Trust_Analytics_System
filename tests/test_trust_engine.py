from datetime import datetime, timezone

from src.analytics.engine import analyze_live_reviews
from src.analytics.sentiment import rating_polarity, score_text_sentiment
from src.schemas import Review


def _r(**kwargs) -> Review:
    base = dict(
        platform="test",
        product_id="p1",
        review_date=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    base.update(kwargs)
    return Review(**base)


def test_sentiment_distinct_from_rating_and_suspicion():
    sent = score_text_sentiment("This product is excellent and I love the quality.")
    assert sent["label"] == "positive"
    assert rating_polarity(1.0) == "negative"
    assert score_text_sentiment(None)["label"] is None


def test_engine_duplicate_text_raises_suspicion_and_has_explanations():
    dup = "This exact template is copied onto many listings for no reason at all."
    reviews = [
        _r(review_id="a", reviewer_id="u1", rating=5, text=dup),
        _r(review_id="b", reviewer_id="u2", rating=5, text=dup),
        _r(review_id="c", reviewer_id="u3", rating=4, text="Totally different wording about battery life and fit."),
    ]
    engine, _ = analyze_live_reviews(reviews)
    assert engine.product_trust_score is not None
    assert engine.trust_level in {"High", "Moderate", "Low"}
    assert engine.explanations
    assert any("repetitive" in r.reasons[0] for r in engine.review_analyses if r.reasons)
    assert engine.sentiment_summary["distinct_from_suspicion_risk"] is True
    assert engine.review_summary["n_reviews"] == 3


def test_engine_does_not_invent_reviewers_without_ids():
    reviews = [
        _r(review_id="1", rating=5, text="Long enough unique review about materials and shipping one."),
        _r(review_id="2", rating=4, text="Long enough unique review about materials and shipping two."),
    ]
    engine, _ = analyze_live_reviews(reviews)
    assert engine.reviewer_analyses == []
    assert engine.reviewer_summary["available"] is False
    assert engine.product_trust_score is not None


def test_engine_empty_reviews_no_fake_score():
    engine, _ = analyze_live_reviews([])
    assert engine.product_trust_score is None
    assert engine.n_reviews == 0
