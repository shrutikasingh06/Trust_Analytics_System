from datetime import datetime, timezone

from src.analytics.live_features import compute_live_features
from src.analytics.trust_scoring import score_from_features
from src.pipeline import analyze_url
from src.platform_detector import detect_platform
from src.schemas import Review


def test_detect_all_supported_platforms():
    cases = {
        "https://www.amazon.in/dp/B0EXAMPLE0": "amazon",
        "https://www.flipkart.com/foo/p/itm123?pid=ABC": "flipkart",
        "https://www.myntra.com/foo": "myntra",
        "https://www.nykaa.com/x": "nykaa",
        "https://www.meesho.com/x": "meesho",
        "https://www.ajio.com/x": "ajio",
        "https://www.croma.com/x": "croma",
        "https://www.reliancedigital.in/x": "reliance_digital",
        "https://www.tatacliq.com/x": "tata_cliq",
    }
    for url, platform in cases.items():
        assert detect_platform(url).platform == platform
    assert detect_platform("https://example.com/x").platform == "unsupported"
    assert detect_platform("not a url").valid_url is False


def test_amazon_marketplace_domain_from_url():
    from src.providers.amazon_provider import amazon_domain_from_url

    assert amazon_domain_from_url("https://www.amazon.in/dp/B0EXAMPLE0") == "amazon.in"
    assert amazon_domain_from_url("https://m.amazon.in/dp/B0EXAMPLE0") == "amazon.in"
    assert amazon_domain_from_url("https://www.amazon.com/dp/B072MQ5BRX") == "amazon.com"
    assert amazon_domain_from_url("https://www.amazon.co.uk/dp/B072MQ5BRX") == "amazon.co.uk"


def test_tracking_query_stripped_and_cart_rejected():
    from src.platform_detector import detect_platform, normalize_url

    cleaned = normalize_url("https://www.amazon.in/dp/B0EXAMPLE0?utm_source=x&tag=store&pid=KEEP")
    assert cleaned is not None
    assert "utm_source" not in cleaned
    assert "tag=" not in cleaned
    assert "pid=KEEP" in cleaned
    cart = detect_platform("https://www.amazon.in/gp/cart/view.html")
    assert cart.listing_page is True
    login = detect_platform("https://www.amazon.in/ap/signin")
    assert login.listing_page is True


def test_product_url_variations_are_not_treated_as_listings():
    from src.platform_detector import detect_platform
    from src.providers.amazon_provider import extract_asin

    myntra = detect_platform(
        "https://www.myntra.com/sarees/kalini/kalini-floral-print-saree/12345678/buy"
    )
    assert myntra.platform == "myntra"
    assert myntra.listing_page is False
    gp = detect_platform("https://www.amazon.com/gp/product/B0EXAMPLE0?th=1#customerReviews")
    assert gp.listing_page is False
    assert extract_asin(gp.normalized_url or "") == "B0EXAMPLE0"
    obidos = extract_asin("https://www.amazon.com/exec/obidos/ASIN/B0EXAMPLE0")
    assert obidos == "B0EXAMPLE0"
    browse = detect_platform("https://www.amazon.com/b?node=172282")
    assert browse.listing_page is True
    offers = detect_platform("https://www.amazon.com/gp/offer-listing/B0EXAMPLE0")
    assert offers.listing_page is True
    search_ref = detect_platform("https://www.amazon.com/s/ref=nb_sb_noss?k=shoes")
    assert search_ref.listing_page is True
    meesho_list = detect_platform("https://www.meesho.com/women-kurtis/pl/3j0")
    assert meesho_list.listing_page is True
    rd_product = detect_platform(
        "https://www.reliancedigital.in/product/samsung-hw-q990cxl-656-w-soundbar-lt5oue-7536837"
    )
    assert rd_product.platform == "reliance_digital"
    assert rd_product.listing_page is False



def test_unconfigured_flipkart_does_not_invent_reviews(monkeypatch):
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_ID", "")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_TOKEN", "")
    monkeypatch.setattr("src.providers.flipkart_provider.SERPAPI_API_KEY", "")
    result = analyze_url("https://www.flipkart.com/product/p/itm123?pid=TESTPID")
    assert result.status == "CATALOG_ONLY"
    assert result.platform == "flipkart"
    assert result.provider_configured is True
    assert result.reviews == []
    assert result.trust_analysis is None
    assert result.data_status == "CATALOG_ONLY"
    assert result.source == "CATALOG DATA AVAILABLE"


def test_unconfigured_myntra():
    result = analyze_url("https://www.myntra.com/some-product")
    assert result.status == "CATALOG_ONLY"
    assert result.data_status == "CATALOG_ONLY"
    assert result.platform == "myntra"
    assert result.data_mode == "CATALOG"
    assert result.product_trust_score is None


def test_unconfigured_platforms_awaiting_providers():
    urls = [
        "https://www.nykaa.com/p/x",
        "https://www.meesho.com/p/x",
        "https://www.ajio.com/p/x",
        "https://www.croma.com/p/x",
        "https://www.reliancedigital.in/p/x",
        "https://www.tatacliq.com/p/x",
    ]
    for url in urls:
        result = analyze_url(url)
        assert result.trust_analysis is None
        assert result.reviews == []
        assert result.data_status == "CATALOG_ONLY"
        assert result.product_trust_score is None


def test_scoring_is_platform_agnostic():
    a = [
        Review(platform="flipkart", product_id="p", review_id="1", reviewer_id="u1", rating=5, text="Unique review text about quality and delivery one."),
        Review(platform="flipkart", product_id="p", review_id="2", reviewer_id="u2", rating=4, text="Unique review text about quality and delivery two."),
    ]
    b = [
        Review(platform="nykaa", product_id="p", review_id="1", reviewer_id="u1", rating=5, text="Unique review text about quality and delivery one."),
        Review(platform="nykaa", product_id="p", review_id="2", reviewer_id="u2", rating=4, text="Unique review text about quality and delivery two."),
    ]
    sa = score_from_features(compute_live_features(a)).product_trust_score
    sb = score_from_features(compute_live_features(b)).product_trust_score
    assert sa == sb


def test_live_scoring_uses_only_supplied_reviews():
    reviews = [
        Review(
            platform="test",
            product_id="p1",
            review_id="r1",
            reviewer_id="u1",
            rating=5,
            text="Excellent build quality and battery life, would buy again.",
            helpful_votes=3,
            review_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            verified_purchase=True,
        ),
        Review(
            platform="test",
            product_id="p1",
            review_id="r2",
            reviewer_id="u2",
            rating=4,
            text="Good product overall, delivery was on time and packing was neat.",
            helpful_votes=1,
            review_date=datetime(2024, 2, 1, tzinfo=timezone.utc),
            verified_purchase=True,
        ),
        Review(
            platform="test",
            product_id="p1",
            review_id="r3",
            reviewer_id="u3",
            rating=2,
            text="Stopped working after two weeks. Support did not help.",
            helpful_votes=4,
            review_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
            verified_purchase=True,
        ),
    ]
    feats = compute_live_features(reviews)
    assert feats["n_reviews"] == 3
    assert feats["duplicate_text_ratio"] == 0.0
    analysis = score_from_features(feats)
    assert analysis.product_trust_score is not None
    assert 0 <= analysis.product_trust_score <= 100
    assert analysis.historical_training_data_used_as_product_reviews is False
    assert analysis.n_reviews_used == 3
    assert analysis.contributing_factors
    assert all(f.feature for f in analysis.contributing_factors)


def test_duplicate_text_lowers_score_relative_to_diverse():
    dup = "This is the exact same template review used many times for this listing."
    clones = [
        Review(platform="t", product_id="p", review_id=str(i), reviewer_id="same", rating=5, text=dup)
        for i in range(10)
    ]
    diverse = [
        Review(
            platform="t",
            product_id="p",
            review_id=str(i),
            reviewer_id=f"u{i}",
            rating=float((i % 5) + 1),
            text=f"Unique opinion number {i} about battery, fit, and price.",
        )
        for i in range(10)
    ]
    s_dup = score_from_features(compute_live_features(clones)).product_trust_score
    s_div = score_from_features(compute_live_features(diverse)).product_trust_score
    assert s_dup is not None and s_div is not None
    assert s_dup < s_div


def test_missing_fields_do_not_crash():
    reviews = [
        Review(platform="t", product_id="p", review_id="1", rating=5, text="ok enough text here"),
        Review(platform="t", product_id="p", review_id="2", rating=4, text="also enough text here"),
    ]
    feats = compute_live_features(reviews)
    assert feats["reviewer_diversity"] is None
    assert feats["verified_purchase_share"] is None
    analysis = score_from_features(feats)
    assert analysis.product_trust_score is not None
