"""
Live integration test of the trust engine.

Uses the currently configured authorized provider only.
Does not scrape. Does not substitute historical parquet.
Does not print API keys.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import SERPAPI_API_KEY, RAINFOREST_API_KEY  # noqa: E402
from src.pipeline import analyze_url  # noqa: E402

# Temporary live integration URL only. Architecture remains platform-agnostic.
LIVE_TEST_URL = "https://www.amazon.com/dp/B072MQ5BRX"


def _strip_reviews_for_report(result_dump: dict) -> dict:
    """Keep proof of live text without dumping every full review body."""
    reviews = result_dump.get("reviews") or []
    result_dump["reviews"] = [
        {
            "review_id": r.get("review_id"),
            "rating": r.get("rating"),
            "text_len": len(r["text"]) if r.get("text") else 0,
            "has_text": bool(r.get("text")),
            "reviewer_id": r.get("reviewer_id"),
        }
        for r in reviews
    ]
    analyses = result_dump.get("review_analyses") or []
    result_dump["review_analyses"] = [
        {
            "review_id": a.get("review_id"),
            "rating": a.get("rating"),
            "rating_polarity": a.get("rating_polarity"),
            "sentiment_label": a.get("sentiment_label"),
            "suspicion_risk": a.get("suspicion_risk"),
            "risk_level": a.get("risk_level"),
            "reasons": a.get("reasons"),
            "review_length_chars": a.get("review_length_chars"),
        }
        for a in analyses
    ]
    return result_dump


def main() -> int:
    if not (SERPAPI_API_KEY or RAINFOREST_API_KEY):
        print("SKIP: no authorized Amazon review provider configured; not fabricating a live test.")
        return 0
    result = analyze_url(LIVE_TEST_URL)
    ok = (
        result.data_status == "LIVE"
        and result.review_count
        and result.review_count > 0
        and result.product_trust_score is not None
        and bool(result.explanations)
        and any(r.text for r in result.reviews)
    )
    dump = _strip_reviews_for_report(result.model_dump(mode="json"))
    out = ROOT / "reports" / "live_trust_engine_sample.json"
    out.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": ok,
                "data_status": result.data_status,
                "platform": result.platform,
                "source": result.source,
                "product_title": result.product.title if result.product else None,
                "review_count": result.review_count,
                "n_reviews_with_text": sum(1 for r in result.reviews if r.text),
                "product_trust_score": result.product_trust_score,
                "trust_level": result.trust_level,
                "suspicion": result.suspicious_review_summary,
                "n_explanations": len(result.explanations),
                "sample_explanation": result.explanations[0] if result.explanations else None,
                "report": str(out),
            },
            indent=2,
        )
    )
    if not ok:
        print("LIVE ENGINE TEST FAILED: missing live reviews, score, or explanations.")
        return 1
    print("LIVE ENGINE TEST PASSED: URL -> live reviews -> features -> analysis -> trust score -> explanations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
