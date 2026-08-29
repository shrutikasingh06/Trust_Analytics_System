"""Orchestrate detect → provider → live and/or exact historical dataset analysis."""
from __future__ import annotations

import logging

from src.analytics.engine import analyze_live_reviews
from src.analytics.fields import merge_availability, product_availability, review_availability
from src.analytics.historical import HistoricalMatch, lookup_historical
from src.config import MAX_REVIEWS, PLATFORM_LABELS
from src.platform_detector import normalize_url
from src.product_ids import canonical_amazon_product_url, extract_catalog_product_id
from src.product_identity import extract_store_product_id
from src.providers.flipkart_provider import extract_flipkart_ids, resolve_flipkart_product_url
from src.providers.page_metadata import catalog_from_url, overlay_product
from src.providers.provider_factory import get_provider
from src.schemas import AnalysisResult, Product, TrustAnalysis

log = logging.getLogger("trust_analytics.pipeline")

UNAVAILABLE_MSG = "Unable to retrieve live review data right now. Please try again."
NOT_CONFIGURED_MSG = "Review analysis is currently unavailable for this platform."
NO_REVIEWS_MSG = (
    "Live review data was not returned for this product, and no historical dataset match is available."
)
CATALOG_SOURCE = "CATALOG DATA AVAILABLE"
CATALOG_ONLY_MSG = (
    "Review-level trust analysis is unavailable because review text is not available from the current data source."
)
CATALOG_HINT = "Product catalog is shown. Missing review text is not filled in, and no trust score is invented."
HISTORICAL_SOURCE = "HISTORICAL DATASET ANALYSIS"
LIVE_SOURCE = "LIVE REVIEW DATA"
BOTH_SOURCE = "LIVE + HISTORICAL AVAILABLE"
UNAVAILABLE_SOURCE = "REVIEW DATA UNAVAILABLE"
HISTORICAL_DISCLAIMER = (
    "These reviews are from the project's historical dataset, not a live storefront feed."
)


def analyze_url(url: str) -> AnalysisResult:
    cleaned = normalize_url(url) or (url or "").strip()
    detection, provider = get_provider(cleaned)
    if not detection.valid_url:
        return AnalysisResult(
            status="INVALID_URL",
            data_status="INVALID_URL",
            platform=None,
            platform_label=None,
            provider=None,
            source=None,
            provider_configured=False,
            data_mode="NONE",
            user_message="Please paste a valid product URL starting with http:// or https://.",
            required_action="Enter a product page URL from a supported store.",
        )
    if detection.platform == "unsupported" or not detection.enabled:
        return AnalysisResult(
            status="UNSUPPORTED_PLATFORM",
            data_status="UNSUPPORTED_PLATFORM",
            platform=detection.platform,
            platform_label=detection.platform_label,
            provider=None,
            source=None,
            provider_configured=False,
            data_mode="NONE",
            user_message="This URL is not from a currently supported e-commerce platform.",
            required_action="Paste a product URL from a supported e-commerce platform.",
        )

    if detection.listing_page:
        return AnalysisResult(
            status="INVALID_URL",
            data_status="INVALID_URL",
            platform=detection.platform,
            platform_label=detection.platform_label,
            provider=None,
            source=None,
            provider_configured=False,
            data_mode="NONE",
            user_message="Please paste a product page URL, not a search or category page.",
            required_action="Open a specific product and copy that page’s URL.",
        )

    assert provider is not None
    fetch_url = detection.normalized_url or cleaned
    catalog_id = extract_store_product_id(detection.platform, url) or extract_store_product_id(
        detection.platform, fetch_url
    )
    if detection.platform == "amazon":
        catalog_id = extract_catalog_product_id("amazon", url) or extract_catalog_product_id(
            "amazon", fetch_url
        )
        canonical = canonical_amazon_product_url(url) or canonical_amazon_product_url(fetch_url)
        if canonical:
            fetch_url = canonical
            if not catalog_id:
                catalog_id = extract_catalog_product_id("amazon", canonical)
    if detection.platform == "flipkart":
        fetch_url = resolve_flipkart_product_url(fetch_url)
        pid, _, _ = extract_flipkart_ids(url)
        if not pid:
            pid, _, _ = extract_flipkart_ids(fetch_url)
        if pid:
            catalog_id = pid
    configured = provider.is_configured()
    product = None
    reviews: list = []
    fetch_error = False
    if detection.platform == "amazon":
        log.info(
            "Amazon product URL accepted catalog_id=%s configured=%s listing=%s",
            catalog_id,
            configured,
            detection.listing_page,
        )

    if configured:
        try:
            product = provider.fetch_product(fetch_url)
            reviews = provider.fetch_reviews(fetch_url, limit=MAX_REVIEWS)
            if catalog_id:
                wanted = catalog_id.upper()
                reviews = [
                    r
                    for r in reviews
                    if not r.product_id or str(r.product_id).upper() == wanted
                ]
                if (
                    detection.platform == "amazon"
                    and product is not None
                    and product.product_id
                    and str(product.product_id).upper() != wanted
                ):
                    log.warning("Live product identifier did not match requested ASIN")
                    product = None
                    reviews = []
        except Exception:
            log.warning("Provider fetch failed for platform=%s", detection.platform)
            fetch_error = True
            product = None
            reviews = []
        if detection.platform == "amazon" and not catalog_id:
            catalog_id = extract_catalog_product_id(
                detection.platform, fetch_url, product.product_id if product else None
            )
        if detection.platform == "flipkart" and product and product.product_id:
            catalog_id = product.product_id

    if detection.platform != "amazon" or not (product and product.title):
        try:
            page_product, page_reviews = catalog_from_url(
                fetch_url,
                detection.platform,
                use_google=detection.platform != "amazon",
            )
            product = overlay_product(product, page_product)
            if not reviews and page_reviews:
                reviews = page_reviews
        except Exception:
            log.info("Public page metadata fallback failed for platform=%s", detection.platform)

    historical = (
        lookup_historical(catalog_id, detection.platform, fetch_url)
        if detection.platform == "amazon" and catalog_id
        else None
    )

    if reviews:
        return _result_from_live(
            detection=detection,
            provider_name=provider.name,
            configured=True,
            product=product,
            reviews=reviews,
            catalog_id=catalog_id,
            historical=historical,
        )

    if historical is not None:
        live_meta = product
        return _result_from_historical(
            detection=detection,
            provider_name=provider.name,
            configured=configured,
            live_product=live_meta,
            historical=historical,
            catalog_id=catalog_id,
        )

    fields = merge_availability(product_availability(product), review_availability([]))
    recovered = bool(product and product.title)
    if fetch_error and not recovered:
        return AnalysisResult(
            status="DATA_ACCESS_UNAVAILABLE",
            data_status="DATA_ACCESS_UNAVAILABLE",
            platform=detection.platform,
            platform_label=detection.platform_label,
            provider=provider.name,
            source=UNAVAILABLE_SOURCE,
            provider_configured=configured,
            data_mode="UNAVAILABLE",
            product=product,
            reviews=[],
            field_availability=fields,
            catalog_product_id=catalog_id,
            user_message=UNAVAILABLE_MSG,
            required_action="Please try again.",
        )
    return AnalysisResult(
        status="CATALOG_ONLY",
        data_status="CATALOG_ONLY",
        platform=detection.platform,
        platform_label=detection.platform_label,
        provider=provider.name,
        source=CATALOG_SOURCE,
        provider_configured=configured,
        data_mode="CATALOG",
        product=product,
        reviews=[],
        field_availability=fields,
        catalog_product_id=catalog_id,
        user_message=CATALOG_ONLY_MSG,
        required_action=CATALOG_HINT,
    )


def _result_from_live(
    *,
    detection,
    provider_name: str,
    configured: bool,
    product: Product | None,
    reviews: list,
    catalog_id: str | None,
    historical: HistoricalMatch | None,
) -> AnalysisResult:
    engine, analysis = analyze_live_reviews(reviews)
    both = historical is not None
    status = "LIVE" if product is not None else "PARTIAL"
    catalog_n = product.review_count if product else None
    summary = dict(engine.review_summary or {})
    summary.update(
        {
            "n_retrieved": len(reviews),
            "n_analyzed": engine.n_reviews,
            "analysis_limit": MAX_REVIEWS,
            "catalog_review_count": catalog_n,
            "limited_by_source": bool(MAX_REVIEWS and len(reviews) >= MAX_REVIEWS),
            "source_kind": "live",
        }
    )
    hist_block = _historical_block(historical) if both else None
    return AnalysisResult(
        status=status,
        data_status="LIVE_AND_HISTORICAL" if both else "LIVE",
        platform=detection.platform,
        platform_label=detection.platform_label or PLATFORM_LABELS.get(detection.platform),
        provider=provider_name,
        source=BOTH_SOURCE if both else LIVE_SOURCE,
        provider_configured=configured,
        data_mode="LIVE_AND_HISTORICAL" if both else "LIVE",
        product=product,
        reviews=reviews,
        field_availability=merge_availability(product_availability(product), review_availability(reviews)),
        trust_analysis=analysis,
        historical_analysis=hist_block,
        catalog_product_id=catalog_id,
        user_message=(
            f"{BOTH_SOURCE}. Live and historical analyses are scored separately and are not merged."
            if both
            else LIVE_SOURCE
        ),
        required_action=None,
        review_count=engine.n_reviews,
        product_trust_score=engine.product_trust_score,
        trust_level=engine.trust_level,
        review_summary=summary,
        sentiment_summary=engine.sentiment_summary,
        suspicious_review_summary=engine.suspicious_review_summary,
        reviewer_summary=engine.reviewer_summary,
        explanations=[e.model_dump() for e in engine.explanations],
        review_analyses=[r.model_dump() for r in engine.review_analyses],
        reviewer_analyses=[r.model_dump() for r in engine.reviewer_analyses],
        trust_breakdown=[c.model_dump() for c in engine.breakdown],
        weighting_notes=engine.weighting_notes,
    )


def _result_from_historical(
    *,
    detection,
    provider_name: str,
    configured: bool,
    live_product: Product | None,
    historical: HistoricalMatch,
    catalog_id: str | None,
) -> AnalysisResult:
    engine, analysis = analyze_live_reviews(historical.reviews)
    analysis.data_mode = "HISTORICAL"
    analysis.historical_training_data_used_as_product_reviews = True
    if analysis.notes:
        analysis.notes[0] = (
            "Scores use historical dataset reviews for this exact product identifier. "
            "They are not a live storefront feed."
        )
    product = _merge_product(live_product, historical.product)
    summary = dict(engine.review_summary or {})
    summary.update(
        {
            "n_retrieved": historical.n_sampled,
            "n_analyzed": engine.n_reviews,
            "n_historical_available": historical.n_available,
            "analysis_limit": MAX_REVIEWS,
            "source_kind": "historical",
            "product_stats": historical.product_stats,
            "review_feature_stats": historical.review_stats,
        }
    )
    fields = merge_availability(product_availability(product), review_availability(historical.reviews))
    return AnalysisResult(
        status="HISTORICAL",
        data_status="HISTORICAL",
        platform=detection.platform,
        platform_label=detection.platform_label or PLATFORM_LABELS.get(detection.platform),
        provider=provider_name,
        source=HISTORICAL_SOURCE,
        provider_configured=configured,
        data_mode="HISTORICAL",
        product=product,
        reviews=historical.reviews,
        field_availability=fields,
        trust_analysis=analysis,
        historical_analysis=_historical_block(historical, engine, analysis),
        catalog_product_id=catalog_id or historical.product_id,
        user_message=f"{HISTORICAL_SOURCE}. {HISTORICAL_DISCLAIMER}",
        required_action=None,
        review_count=engine.n_reviews,
        product_trust_score=engine.product_trust_score,
        trust_level=engine.trust_level,
        review_summary=summary,
        sentiment_summary=engine.sentiment_summary,
        suspicious_review_summary=engine.suspicious_review_summary,
        reviewer_summary=engine.reviewer_summary,
        explanations=[e.model_dump() for e in engine.explanations],
        review_analyses=[r.model_dump() for r in engine.review_analyses],
        reviewer_analyses=[r.model_dump() for r in engine.reviewer_analyses],
        trust_breakdown=[c.model_dump() for c in engine.breakdown],
        weighting_notes=engine.weighting_notes,
    )


def _historical_block(
    match: HistoricalMatch | None,
    engine=None,
    analysis: TrustAnalysis | None = None,
) -> dict | None:
    if match is None:
        return None
    if engine is None:
        engine, analysis = analyze_live_reviews(match.reviews)
        if analysis:
            analysis.data_mode = "HISTORICAL"
            analysis.historical_training_data_used_as_product_reviews = True
    summary = dict(engine.review_summary or {})
    summary.update(
        {
            "n_retrieved": match.n_sampled,
            "n_analyzed": engine.n_reviews,
            "n_historical_available": match.n_available,
            "source_kind": "historical",
            "product_stats": match.product_stats,
            "review_feature_stats": match.review_stats,
        }
    )
    return {
        "source": HISTORICAL_SOURCE,
        "disclaimer": HISTORICAL_DISCLAIMER,
        "product_id": match.product_id,
        "n_available": match.n_available,
        "n_sampled": match.n_sampled,
        "product_stats": match.product_stats,
        "review_feature_stats": match.review_stats,
        "product_trust_score": engine.product_trust_score,
        "trust_level": engine.trust_level,
        "review_summary": summary,
        "sentiment_summary": engine.sentiment_summary,
        "suspicious_review_summary": engine.suspicious_review_summary,
        "reviewer_summary": engine.reviewer_summary,
        "explanations": [e.model_dump() for e in engine.explanations],
        "review_analyses": [r.model_dump() for r in engine.review_analyses],
        "reviewer_analyses": [r.model_dump() for r in engine.reviewer_analyses],
        "trust_breakdown": [c.model_dump() for c in engine.breakdown],
        "reviews": [r.model_dump(mode="json") for r in match.reviews],
        "trust_analysis": analysis.model_dump(mode="json") if analysis else None,
    }


def _merge_product(live: Product | None, hist: Product | None) -> Product | None:
    if live is None:
        return hist
    if hist is None:
        return live
    return live.model_copy(
        update={
            "category": live.category or hist.category,
            "rating": live.rating if live.rating is not None else hist.rating,
            "review_count": live.review_count if live.review_count is not None else hist.review_count,
            "product_id": live.product_id or hist.product_id,
            "price": live.price,
            "currency": live.currency,
        }
    )
