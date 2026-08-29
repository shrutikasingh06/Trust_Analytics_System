from src.pipeline import analyze_url
from src.config import SERPAPI_API_KEY, RAINFOREST_API_KEY
from fastapi.testclient import TestClient

from src.app import app, _strip_secrets

client = TestClient(app)


def test_home_is_universal_not_storefront_specific():
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "Universal" not in html
    assert html.count("E-Commerce Trust Analytics") == 2
    assert "<h1>E-Commerce Trust Analytics</h1>" in html
    assert html.count("<h1") == 1
    assert "Universal" not in html
    assert "E-commerce trust analytics" not in html
    assert "Analyze product reviews and understand trust, sentiment, reviewer behavior, and suspicious-review risk." in html
    assert "Paste a product URL" in html
    assert "/static/app.js?v=" in html
    assert "Universal" not in html
    for name in ["Flipkart", "Myntra", "Nykaa", "Meesho", "Ajio", "Croma", "Tata CLiQ"]:
        assert name in html
    assert "SERPAPI" not in html
    assert "SerpAPI" not in html
    assert "administrator" not in html.lower()
    assert "api_key" not in html.lower()


def test_analyze_unconfigured_platform_is_not_live():
    res = client.post("/api/analyze", json={"url": "https://www.myntra.com/dresses/example"})
    assert res.status_code == 200
    data = res.json()
    assert data["data_status"] == "CATALOG_ONLY"
    assert data["product_trust_score"] is None
    assert data["trust_analysis"] is None
    blob = str(data).lower()
    assert "api_key" not in blob
    assert "serpapi" not in blob
    assert data["platform"] == "myntra"
    assert data.get("product")
    assert data.get("reviews") in (None, [])


def test_detect_does_not_claim_live():
    res = client.post("/api/detect", json={"url": "https://www.flipkart.com/p/itm123"})
    data = res.json()
    assert data["platform"] == "flipkart"
    assert "live" not in data or data.get("live") is not True


def test_http_analyze_live_url_when_provider_configured():
    if not (SERPAPI_API_KEY or RAINFOREST_API_KEY):
        return
    res = client.post("/api/analyze", json={"url": "https://www.amazon.com/dp/B072MQ5BRX"})
    assert res.status_code == 200
    data = res.json()
    assert data["data_status"] == "LIVE"
    assert data["review_count"] >= 1
    assert data["product_trust_score"] is not None
    assert data["explanations"]
    blob = str(data).lower()
    assert "api_key" not in blob
    assert "serpapi" not in blob
    assert "rainforest" not in blob
    if SERPAPI_API_KEY:
        assert SERPAPI_API_KEY not in str(data)
    for item in data["explanations"]:
        assert "bfr_" not in str(item.get("feature", "")).lower()
        assert "rating_std" not in str(item.get("feature", "")).lower()


def test_analyze_does_not_load_historical_parquet():
    import inspect
    import sys

    import src.pipeline as pipeline

    src_text = inspect.getsource(pipeline)
    assert "read_parquet" not in src_text
    assert "review_features.parquet" not in src_text
    analyze_url("https://www.nykaa.com/example")
    assert "scripts.features_common" not in sys.modules
    assert "scripts.build_review_features" not in sys.modules


def test_all_supported_hosts_detect_without_claiming_live():
    from src.platform_detector import detect_platform

    samples = {
        "https://www.amazon.com/dp/B072MQ5BRX": "amazon",
        "https://www.flipkart.com/p/itm123": "flipkart",
        "https://www.myntra.com/x": "myntra",
        "https://www.nykaa.com/x": "nykaa",
        "https://www.meesho.com/x": "meesho",
        "https://www.ajio.com/x": "ajio",
        "https://www.croma.com/x": "croma",
        "https://www.reliancedigital.in/x": "reliance_digital",
        "https://www.tatacliq.com/x": "tata_cliq",
    }
    for url, plat in samples.items():
        d = detect_platform(url)
        assert d.platform == plat
        if plat == "amazon":
            continue
        res = client.post("/api/analyze", json={"url": url})
        body = res.json()
        assert body["data_status"] in {"NOT_CONFIGURED", "DATA_ACCESS_UNAVAILABLE", "CATALOG_ONLY"}
        assert body.get("product_trust_score") is None
        assert not body.get("reviews")
        assert body.get("sentiment_summary") in (None, {})
        assert "api_key" not in str(body).lower()


def test_strip_secrets_removes_keys():
    cleaned = _strip_secrets({"api_key": "secret", "ok": 1, "link": "https://x?api_key=abc"})
    assert "api_key" not in cleaned
    assert cleaned["ok"] == 1
    assert cleaned["link"] == "[redacted]"


def test_empty_and_whitespace_url_are_clean():
    empty = client.post("/api/analyze", json={"url": ""})
    assert empty.status_code == 200
    assert empty.json()["data_status"] == "INVALID_URL"
    assert empty.json()["product_trust_score"] is None
    space = client.post("/api/analyze", json={"url": "   "})
    assert space.status_code == 200
    assert space.json()["data_status"] == "INVALID_URL"


def test_invalid_and_unsupported_urls():
    bad = client.post("/api/analyze", json={"url": "not a url"})
    assert bad.status_code == 200
    assert bad.json()["data_status"] == "INVALID_URL"
    other = client.post("/api/analyze", json={"url": "https://example.com/product/1"})
    assert other.json()["data_status"] == "UNSUPPORTED_PLATFORM"
    assert other.json()["product_trust_score"] is None


def test_search_url_is_rejected_without_provider_call():
    res = client.post("/api/analyze", json={"url": "https://www.amazon.com/s?k=coffee"})
    assert res.status_code == 200
    data = res.json()
    assert data["data_status"] == "INVALID_URL"
    assert data["platform"] == "amazon"
    assert data["product_trust_score"] is None
    assert not data.get("reviews")


def test_category_and_offer_urls_rejected_without_provider_call():
    cat = client.post("/api/analyze", json={"url": "https://www.amazon.com/b?node=172282"})
    assert cat.json()["data_status"] == "INVALID_URL"
    assert cat.json()["product_trust_score"] is None
    offers = client.post("/api/analyze", json={"url": "https://www.amazon.com/gp/offer-listing/B0EXAMPLE0"})
    assert offers.json()["data_status"] == "INVALID_URL"


def test_frontend_does_not_force_try_again_on_source_limits():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function liveStatusLabel(status, data)" in js
    assert "data.user_message" in js


def test_provider_failure_returns_unavailable(monkeypatch):
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.is_configured",
        lambda self: True,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.fetch_product",
        lambda self, url: (_ for _ in ()).throw(RuntimeError("simulated failure")),
    )
    res = client.post("/api/analyze", json={"url": "https://www.amazon.com/dp/B072MQ5BRX"})
    assert res.status_code == 200
    data = res.json()
    assert data["data_status"] == "DATA_ACCESS_UNAVAILABLE"
    assert data["product_trust_score"] is None
    assert data.get("reviews") == []
    assert "try again" in data["user_message"].lower()
    assert "api_key" not in str(data).lower()


def test_product_without_reviews_explains_source_limit(monkeypatch):
    from src.schemas import Product

    product = Product(
        platform="amazon",
        product_id="B0TESTTEST",
        url="https://www.amazon.in/dp/B0TESTTEST",
        title="Example product",
        price=949,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.is_configured",
        lambda self: True,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.fetch_product",
        lambda self, url: product,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.fetch_reviews",
        lambda self, url, limit=None: [],
    )
    res = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0TESTTEST"})
    data = res.json()
    assert data["data_status"] == "CATALOG_ONLY"
    assert data["product_trust_score"] is None
    assert data.get("reviews") == []
    assert data.get("product", {}).get("title") == "Example product"
    assert "review text" in data["user_message"].lower()
    assert "try again" not in data["user_message"].lower()
    assert "api_key" not in str(data).lower()


def test_query_fragment_and_http_still_detect():
    from src.platform_detector import detect_platform, normalize_url

    frag = detect_platform("https://www.flipkart.com/foo/p/itm123?pid=ABC#reviews")
    assert frag.platform == "flipkart"
    assert frag.listing_page is False
    assert "#" not in (frag.normalized_url or "")
    http = detect_platform("http://www.myntra.com/some-product")
    assert http.platform == "myntra"
    mobile = detect_platform("https://m.nykaa.com/p/x")
    assert mobile.platform == "nykaa"
    bare = detect_platform("www.ajio.com/p/x")
    assert bare.platform == "ajio"
    assert normalize_url("  https://www.croma.com/p/x  ").startswith("https://")


def _clear_amazon_cache(monkeypatch=None):
    from src.providers.amazon_provider import AmazonProvider
    from src.providers.provider_factory import get_provider_by_platform

    prov = get_provider_by_platform("amazon")
    if prov is not None:
        getattr(prov, "_serpapi_product_cache", {}).clear()
    if monkeypatch is not None:
        monkeypatch.setattr(AmazonProvider, "_get_text", lambda self, endpoint, params: None)


def test_serpapi_reviews_skip_rainforest_fallback(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    calls = {"rf": 0}
    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "rf-test-key")
    _clear_amazon_cache(monkeypatch)

    def fake_get(self, endpoint, params):
        if "rainforestapi.com" in endpoint:
            calls["rf"] += 1
            return None
        return {
            "product_results": {"title": "Sample coffee", "asin": "B0REVSERP1"},
            "reviews_information": {
                "authors_reviews": [
                    {"position": 1, "text": "Great coffee for everyday brewing at home.", "rating": 5},
                    {"position": 2, "text": "Tastes fresh and the bag lasted a long time.", "rating": 4},
                ]
            },
        }

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    res = client.post("/api/analyze", json={"url": "https://www.amazon.com/dp/B0REVSERP1"})
    data = res.json()
    assert data["data_status"] == "LIVE"
    assert data["review_count"] == 2
    assert data["product_trust_score"] is not None
    assert calls["rf"] == 0
    blob = str(data)
    assert "serp-test-key" not in blob
    assert "rf-test-key" not in blob
    assert "api_key" not in blob.lower()


def test_rainforest_fallback_when_serpapi_has_product_without_reviews(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    calls = {"rf_reviews": 0}
    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "rf-test-key")
    _clear_amazon_cache(monkeypatch)

    def fake_get(self, endpoint, params):
        if "rainforestapi.com" in endpoint:
            if params.get("type") == "reviews":
                calls["rf_reviews"] += 1
                return {
                    "reviews": [
                        {"id": "r1", "rating": 5, "body": "Fabric quality is excellent and stitching looks neat.", "profile": {"id": "u1"}},
                        {"id": "r2", "rating": 4, "body": "Colour is as shown and delivery was on time.", "profile": {"id": "u2"}},
                    ]
                }
            return None
        return {
            "product_results": {"title": "Sample kurta set", "asin": "B0NOTEXT01"},
            "reviews_information": {"summary": {"customer_reviews": {"5 star": 10}}},
        }

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    res = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0NOTEXT01"})
    data = res.json()
    assert calls["rf_reviews"] == 1
    assert data["data_status"] == "LIVE"
    assert data["review_count"] == 2
    assert data["product_trust_score"] is not None
    assert "serp-test-key" not in str(data)
    assert "rf-test-key" not in str(data)


def test_both_sources_without_reviews_unavailable(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "rf-test-key")
    _clear_amazon_cache(monkeypatch)

    def fake_get(self, endpoint, params):
        if "rainforestapi.com" in endpoint:
            return {"reviews": []}
        return {
            "product_results": {"title": "Sample kurta set", "asin": "B0BOTHNONE"},
            "reviews_information": {"summary": {"customer_reviews": {"5 star": 3}}},
        }

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    res = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0BOTHNONE"})
    data = res.json()
    assert data["data_status"] == "CATALOG_ONLY"
    assert data["product_trust_score"] is None
    assert data.get("reviews") == []
    assert "review text" in data["user_message"].lower()


def test_no_rainforest_key_skips_fallback(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    calls = {"rf": 0}
    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "")
    _clear_amazon_cache(monkeypatch)

    def fake_get(self, endpoint, params):
        if "rainforestapi.com" in endpoint:
            calls["rf"] += 1
            return {"reviews": [{"id": "x", "body": "should not be used", "rating": 5}]}
        return {
            "product_results": {"title": "Sample kurta set", "asin": "B0NORFKEY0"},
            "reviews_information": {"summary": {"customer_reviews": {"5 star": 3}}},
        }

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    res = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0NORFKEY0"})
    data = res.json()
    assert calls["rf"] == 0
    assert data["data_status"] == "CATALOG_ONLY"
    assert data["product_trust_score"] is None


def test_serpapi_complete_failure_uses_rainforest_once(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    calls = {"rf_reviews": 0, "rf_product": 0, "serp": 0}
    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "rf-test-key")
    _clear_amazon_cache(monkeypatch)

    def fake_get(self, endpoint, params):
        if "rainforestapi.com" in endpoint:
            if params.get("type") == "reviews":
                calls["rf_reviews"] += 1
                return {
                    "reviews": [
                        {"id": "r1", "rating": 5, "body": "Quality is good and the fit is accurate.", "profile": {"id": "u1"}},
                        {"id": "r2", "rating": 4, "body": "Nice fabric though delivery took longer.", "profile": {"id": "u2"}},
                    ]
                }
            calls["rf_product"] += 1
            return {"request_info": {"success": True}, "product": {"title": "RF product", "asin": "B0RFONLY01"}}
        calls["serp"] += 1
        return {"error": "Amazon Product API hasn't returned any results for this query."}

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    res = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0RFONLY01"})
    data = res.json()
    assert calls["serp"] >= 1
    assert calls["rf_reviews"] >= 1
    assert data["data_status"] == "LIVE"
    assert data["review_count"] == 2
    assert data["product_trust_score"] is not None
    assert "serp-test-key" not in str(data)
    assert "rf-test-key" not in str(data)


def test_historical_exact_asin_match_is_not_labeled_live(monkeypatch):
    from pathlib import Path

    from src.analytics.historical import PRODUCT_FEATURES, clear_historical_cache

    if not PRODUCT_FEATURES.is_file():
        return
    clear_historical_cache()
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.is_configured",
        lambda self: True,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.fetch_product",
        lambda self, url: None,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.fetch_reviews",
        lambda self, url, limit=None: [],
    )
    res = client.post("/api/analyze", json={"url": "https://www.amazon.com/dp/B00004Y2MM"})
    data = res.json()
    assert data["data_status"] == "HISTORICAL"
    assert data["data_mode"] == "HISTORICAL"
    assert data["source"] == "HISTORICAL DATASET ANALYSIS"
    assert data["catalog_product_id"] == "B00004Y2MM"
    assert data["review_count"] >= 1
    assert data["product_trust_score"] is not None
    assert data["review_summary"]["n_historical_available"] == data["product"]["review_count"]
    assert data["review_summary"]["n_historical_available"] == 68
    assert data["review_summary"]["n_historical_available"] != 21897475
    assert all((r.get("product_id") == "B00004Y2MM") for r in (data.get("reviews") or []))
    assert "live storefront" in data["user_message"].lower()
    assert data["source"] != "LIVE REVIEW DATA"
    blob = str(data).lower()
    assert "api_key" not in blob
    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "HISTORICAL DATASET ANALYSIS" in js
    assert "LIVE REVIEW DATA" in js
    assert "REVIEW DATA UNAVAILABLE" in js


def test_unmatched_asin_does_not_use_another_product(monkeypatch):
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.is_configured",
        lambda self: True,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.fetch_product",
        lambda self, url: None,
    )
    monkeypatch.setattr(
        "src.providers.amazon_provider.AmazonProvider.fetch_reviews",
        lambda self, url, limit=None: [],
    )
    res = client.post("/api/analyze", json={"url": "https://www.amazon.com/dp/B0NOTINDS1"})
    data = res.json()
    assert data["data_status"] == "CATALOG_ONLY"


def test_catalog_id_extracted_from_amazon_url_variations():
    from src.product_ids import extract_catalog_product_id, extract_asin, canonical_amazon_product_url
    from src.platform_detector import detect_platform

    assert extract_catalog_product_id("amazon", "https://www.amazon.com/dp/B00004Y2MM?tag=x") == "B00004Y2MM"
    assert extract_asin("https://www.amazon.in/mh/?_encoding=UTF8&s=B0EXAMPLE0") == "B0EXAMPLE0"
    assert extract_asin("https://www.amazon.com/some-title/dp/B0EXAMPLE0/ref=sr_1_1") == "B0EXAMPLE0"
    assert extract_asin("https://www.amazon.com/gp/aw/d/B0EXAMPLE0") == "B0EXAMPLE0"
    assert extract_asin("https://www.amazon.in/foo?pd_rd_i=B0EXAMPLE0") == "B0EXAMPLE0"
    assert extract_asin("https://www.amazon.com/s?k=coffee") is None
    hub = detect_platform("https://www.amazon.in/mh/?_encoding=UTF8&ref_=nav&s=B0EXAMPLE0")
    assert hub.platform == "amazon"
    assert hub.listing_page is False
    assert hub.normalized_url == "https://www.amazon.in/dp/B0EXAMPLE0"
    assert canonical_amazon_product_url("https://www.amazon.in/mh/?s=B0EXAMPLE0") == "https://www.amazon.in/dp/B0EXAMPLE0"
    assert extract_catalog_product_id("flipkart", "https://www.flipkart.com/p/itm123?pid=TESTPID") is None
    assert extract_catalog_product_id("myntra", "https://www.myntra.com/sarees/kalini/123/buy") is None
    assert (
        extract_catalog_product_id(
            "flipkart",
            "https://www.flipkart.com/camera/p/itm123?pid=B00004Y2MM",
        )
        is None
    )


def test_non_amazon_urls_never_query_historical_asin_corpus(monkeypatch):
    calls = {"n": 0}

    def forbidden(product_id, platform, url):
        calls["n"] += 1
        raise AssertionError(f"historical lookup must not run for {platform}")

    monkeypatch.setattr("src.pipeline.lookup_historical", forbidden)
    urls = [
        "https://www.flipkart.com/camera/p/itm123?pid=B00004Y2MM",
        "https://www.myntra.com/sarees/kalini/B00004Y2MM/buy",
        "https://www.nykaa.com/p/B00004Y2MM",
        "https://www.meesho.com/p/B00004Y2MM",
        "https://www.ajio.com/p/B00004Y2MM",
        "https://www.croma.com/p/B00004Y2MM",
        "https://www.reliancedigital.in/p/B00004Y2MM",
        "https://www.tatacliq.com/p/B00004Y2MM",
    ]
    for url in urls:
        res = client.post("/api/analyze", json={"url": url})
        data = res.json()
        assert data["data_status"] in {"NOT_CONFIGURED", "DATA_ACCESS_UNAVAILABLE", "CATALOG_ONLY"}
        assert data.get("historical_analysis") in (None, {})
        assert data.get("product_trust_score") is None
        assert data.get("source") != "HISTORICAL DATASET ANALYSIS"
        assert data.get("source") != "LIVE REVIEW DATA"
    assert calls["n"] == 0


def _two_reviews(asin: str):
    from src.schemas import Review

    return [
        Review(platform="amazon", product_id=asin, review_id=f"{asin}-1", reviewer_id="u1", rating=5, text="Excellent build quality and the fit is accurate for daily use."),
        Review(platform="amazon", product_id=asin, review_id=f"{asin}-2", reviewer_id="u2", rating=4, text="Good product overall though delivery took a little longer than expected."),
    ]


def test_asin_not_in_dataset_still_reaches_live_provider(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider
    from src.schemas import Product

    calls = {"product": 0, "reviews": 0, "hist": 0}
    asin = "B0LIVEONLY"
    monkeypatch.setattr(AmazonProvider, "is_configured", lambda self: True)

    def fp(self, url):
        calls["product"] += 1
        assert asin in url.upper()
        return Product(platform="amazon", product_id=asin, url=url, title="Live-only product", price=16.62, currency="USD", extra={"marketplace": "amazon.com"})

    def fr(self, url, limit=None):
        calls["reviews"] += 1
        return _two_reviews(asin)

    def hist(product_id, platform, url):
        calls["hist"] += 1
        assert platform == "amazon"
        return None

    monkeypatch.setattr(AmazonProvider, "fetch_product", fp)
    monkeypatch.setattr(AmazonProvider, "fetch_reviews", fr)
    monkeypatch.setattr("src.pipeline.lookup_historical", hist)
    res = client.post("/api/analyze", json={"url": f"https://www.amazon.com/dp/{asin}?utm_source=x"})
    data = res.json()
    assert calls["product"] == 1
    assert calls["reviews"] == 1
    assert calls["hist"] == 1
    assert data["data_status"] == "LIVE"
    assert data["catalog_product_id"] == asin
    assert data["product"]["price"] == 16.62
    assert data["product"]["currency"] == "USD"
    assert data.get("historical_analysis") in (None, {})
    assert data["product_trust_score"] is not None


def test_amazon_hub_and_slug_urls_reach_live_provider(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider
    from src.schemas import Product

    asin = "B0HUBASIN1"
    seen = []
    monkeypatch.setattr(AmazonProvider, "is_configured", lambda self: True)

    def fp(self, url):
        seen.append(url)
        return Product(
            platform="amazon",
            product_id=asin,
            url=url,
            title="Hub product",
            price=949,
            currency="INR",
            extra={"marketplace": "amazon.in"},
        )

    monkeypatch.setattr(AmazonProvider, "fetch_product", fp)
    monkeypatch.setattr(AmazonProvider, "fetch_reviews", lambda self, url, limit=None: _two_reviews(asin))
    monkeypatch.setattr("src.pipeline.lookup_historical", lambda *a, **k: None)
    data = client.post(
        "/api/analyze",
        json={"url": f"https://www.amazon.in/mh/?_encoding=UTF8&ref_=nav_mission&s={asin}"},
    ).json()
    assert data["data_status"] == "LIVE"
    assert data["catalog_product_id"] == asin
    assert data["product"]["currency"] == "INR"
    assert seen
    assert all(u == f"https://www.amazon.in/dp/{asin}" for u in seen)
    slug = client.post(
        "/api/analyze",
        json={"url": f"https://www.amazon.com/some-product-name/dp/{asin}/ref=sr_1_1"},
    ).json()
    assert slug["data_status"] == "LIVE"
    assert slug["catalog_product_id"] == asin


def test_live_and_historical_are_separate_for_generic_asin(monkeypatch):
    from src.analytics.historical import HistoricalMatch
    from src.providers.amazon_provider import AmazonProvider
    from src.schemas import Product

    asin = "B0BOTH0001"
    monkeypatch.setattr(AmazonProvider, "is_configured", lambda self: True)
    monkeypatch.setattr(
        AmazonProvider,
        "fetch_product",
        lambda self, url: Product(platform="amazon", product_id=asin, url=url, title="Both sources", price=9.99, currency="USD"),
    )
    monkeypatch.setattr(AmazonProvider, "fetch_reviews", lambda self, url, limit=None: _two_reviews(asin))

    def hist(product_id, platform, url):
        assert product_id == asin
        reviews = _two_reviews(asin)
        return HistoricalMatch(
            product_id=asin,
            n_available=40,
            n_sampled=2,
            product_stats={"review_count": 40, "avg_rating": 4.2},
            reviews=reviews,
            product=Product(platform="amazon", product_id=asin, url=url, title=None),
        )

    monkeypatch.setattr("src.pipeline.lookup_historical", hist)
    data = client.post("/api/analyze", json={"url": f"https://www.amazon.com/dp/{asin}"}).json()
    assert data["data_status"] == "LIVE_AND_HISTORICAL"
    assert data["source"] == "LIVE + HISTORICAL AVAILABLE"
    assert data["historical_analysis"]["source"] == "HISTORICAL DATASET ANALYSIS"
    assert data["review_count"] == 2


def test_neither_live_nor_historical_is_unavailable(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider
    from src.schemas import Product

    asin = "B0NOHIST01"
    monkeypatch.setattr(AmazonProvider, "is_configured", lambda self: True)
    monkeypatch.setattr(
        AmazonProvider,
        "fetch_product",
        lambda self, url: Product(platform="amazon", product_id=asin, url=url, title="No reviews"),
    )
    monkeypatch.setattr(AmazonProvider, "fetch_reviews", lambda self, url, limit=None: [])
    monkeypatch.setattr("src.pipeline.lookup_historical", lambda *a, **k: None)
    data = client.post("/api/analyze", json={"url": f"https://www.amazon.com/dp/{asin}"}).json()
    assert data["data_status"] == "CATALOG_ONLY"
    assert data["product_trust_score"] is None
    assert data.get("reviews") == []
    assert "review text" in data["user_message"].lower()


def test_src_has_no_hardcoded_amazon_asin_whitelist():
    from pathlib import Path

    banned = ("B072MQ5BRX", "B00004Y2MM", "B0H6MJ4VDS")
    root = Path(__file__).resolve().parents[1] / "src"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for asin in banned:
            assert asin not in text, f"{path} must not hard-code {asin}"


def test_cache_and_price_do_not_leak_across_asins(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "")
    _clear_amazon_cache(monkeypatch)
    monkeypatch.setattr("src.pipeline.lookup_historical", lambda *a, **k: None)

    def fake_get(self, endpoint, params):
        asin = params.get("asin")
        domain = params.get("amazon_domain")
        if asin == "B0AAA11111":
            assert domain == "amazon.com"
            return {
                "product_results": {
                    "title": "Product A",
                    "asin": "B0AAA11111",
                    "price": {"extracted_value": 16.62, "currency": "USD"},
                },
                "reviews_information": {
                    "authors_reviews": [
                        {"position": 1, "text": "Great coffee for everyday brewing at home.", "rating": 5},
                        {"position": 2, "text": "Tastes fresh and the bag lasted a long time.", "rating": 4},
                    ]
                },
            }
        if asin == "B0BBB22222":
            assert domain == "amazon.in"
            return {
                "product_results": {
                    "title": "Product B",
                    "asin": "B0BBB22222",
                    "price": {"extracted_value": 949, "currency": "INR"},
                },
                "reviews_information": {"authors_reviews": []},
            }
        raise AssertionError(f"unexpected asin {asin}")

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    a = client.post("/api/analyze", json={"url": "https://www.amazon.com/dp/B0AAA11111"}).json()
    b = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0BBB22222"}).json()
    assert a["product"]["title"] == "Product A"
    assert a["product"]["price"] == 16.62
    assert a["product"]["currency"] == "USD"
    assert a["data_status"] == "LIVE"
    assert b["product"]["title"] == "Product B"
    assert b["product"]["price"] == 949
    assert b["product"]["currency"] == "INR"
    assert b["product"]["title"] != a["product"]["title"]
    assert all(r["product_id"] == "B0AAA11111" for r in a["reviews"])


def test_currency_not_guessed_when_provider_omits_it(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "")
    _clear_amazon_cache(monkeypatch)
    monkeypatch.setattr("src.pipeline.lookup_historical", lambda *a, **k: None)

    def fake_get(self, endpoint, params):
        return {
            "product_results": {"title": "No currency", "asin": "B0NOCURR01", "extracted_price": 12.5},
            "reviews_information": {"authors_reviews": []},
        }

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    data = client.post("/api/analyze", json={"url": "https://www.amazon.com/dp/B0NOCURR01"}).json()
    assert data["product"]["price"] == 12.5
    assert data["product"]["currency"] is None


def test_frontend_clears_results_and_formats_currency():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert js.index("hideResults()") < js.index('fetch("/api/analyze"')
    assert "function formatPriceDisplay" in js
    assert "currency unavailable" in js
    assert "Price unavailable" in js


def test_serpapi_brand_and_nested_reviews(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "")
    _clear_amazon_cache(monkeypatch)

    def fake_get(self, endpoint, params):
        return {
            "product_results": {
                "title": "Embroidered set",
                "asin": "B0BRAND001",
                "brand": "Kalash Defining Beauty",
                "price": {"extracted_value": 949, "currency": "INR"},
            },
            "product_details": {},
            "reviews_information": {
                "summary": {"customer_reviews": {"5 star": 80, "4 star": 10, "3 star": 5, "2 star": 3, "1 star": 2}},
                "nested": {
                    "top_reviews": [
                        {"position": 1, "text": "Fabric quality is excellent and the embroidery looks neat.", "rating": 5},
                        {"position": 2, "text": "Colour is as shown though delivery took a little longer.", "rating": 4},
                    ]
                },
            },
        }

    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    data = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0BRAND001"}).json()
    assert data["data_status"] == "LIVE"
    assert data["product"]["brand"] == "Kalash Defining Beauty"
    assert data["product"]["currency"] == "INR"
    assert data["review_count"] == 2
    assert data["product"]["rating"] is not None


def test_authorized_html_reviews_used_when_json_has_no_bodies(monkeypatch):
    from src.providers.amazon_provider import AmazonProvider

    monkeypatch.setattr("src.providers.amazon_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.amazon_provider.RAINFOREST_API_KEY", "")
    _clear_amazon_cache()
    monkeypatch.setattr("src.pipeline.lookup_historical", lambda *a, **k: None)

    def fake_get(self, endpoint, params):
        return {
            "product_results": {"title": "HTML fallback product", "asin": "B0HTMLREV1", "extracted_price": 10, "price": {"extracted_value": 10, "currency": "USD"}},
            "reviews_information": {"summary": {"customer_reviews": {"5 star": 4}}},
        }

    html = """
    <div id="customer_review-R1">
      <i data-hook="review-star-rating"><span>5.0 out of 5 stars</span></i>
      <a data-hook="review-title"><span>5.0 out of 5 stars</span><span>Excellent fit</span></a>
      <span data-hook="review-body"><span>The stitching is strong and the fabric feels comfortable for daily wear.</span></span>
    </div>
    <div id="customer_review-R2">
      <i data-hook="review-star-rating"><span>4.0 out of 5 stars</span></i>
      <a data-hook="review-title"><span>4.0 out of 5 stars</span><span>Good value</span></a>
      <span data-hook="review-body"><span>Colour matched the listing and delivery was reasonably quick overall.</span></span>
    </div>
    """
    monkeypatch.setattr(AmazonProvider, "_get_json", fake_get)
    monkeypatch.setattr(AmazonProvider, "_get_text", lambda self, endpoint, params: html)
    data = client.post("/api/analyze", json={"url": "https://www.amazon.in/dp/B0HTMLREV1"}).json()
    assert data["data_status"] == "LIVE"
    assert data["review_count"] == 2
    assert data["product_trust_score"] is not None
    assert "fabric feels comfortable" in (data["reviews"][0]["text"] or "")


def test_flipkart_matching_pid_uses_serpapi_google(monkeypatch):
    from src.providers.flipkart_provider import FlipkartProvider
    from src.providers.provider_factory import get_provider_by_platform

    monkeypatch.setattr("src.providers.flipkart_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_ID", "")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_TOKEN", "")
    pid = "SWDHGYBKHHHB47PM"
    payload = {
        "organic_results": [
            {
                "title": "PILUDI Embroidered Kurta Set",
                "link": f"https://www.flipkart.com/piludi-set/p/itmd4f87f61754c9?pid={pid}",
                "snippet": "Stitching is neat and the fabric feels comfortable for daily wear at home.",
                "rich_snippet": {
                    "top": {
                        "detected_extensions": {
                            "rating": 3.6,
                            "reviews": 226,
                            "price_from": 637,
                            "currency": "₹",
                        }
                    }
                },
            }
        ]
    }
    monkeypatch.setattr(FlipkartProvider, "_google_search", lambda self, q: payload)
    prov = get_provider_by_platform("flipkart")
    if prov is not None:
        getattr(prov, "_cache", {}).clear()
    data = client.post(
        "/api/analyze",
        json={"url": f"https://www.flipkart.com/piludi-embroidered-kurta-salwar-dupatta-set/p/itmd4f87f61754c9?pid={pid}"},
    ).json()
    assert data["platform"] == "flipkart"
    assert data["data_status"] == "LIVE"
    assert data["review_count"] >= 1
    assert data["product"]["price"] == 637
    assert data["product"]["currency"] == "INR"
    assert data["product_trust_score"] is not None
    assert "serp-test-key" not in str(data)


def test_flipkart_deal_snippet_is_not_treated_as_a_review(monkeypatch):
    from src.providers.flipkart_provider import FlipkartProvider
    from src.providers.provider_factory import get_provider_by_platform

    monkeypatch.setattr("src.providers.flipkart_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_ID", "")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_TOKEN", "")
    pid = "SWDHGYBKHHHB47PM"
    payload = {
        "organic_results": [
            {
                "title": "PILUDI Embroidered Kurta Set",
                "link": f"https://www.flipkart.com/x/p/itmabc?pid={pid}",
                "snippet": "68% OFF. Hot Deal. Free delivery. In stock.",
                "rich_snippet": {"top": {"detected_extensions": {"price_from": 637, "currency": "₹"}}},
            }
        ]
    }
    monkeypatch.setattr(FlipkartProvider, "_google_search", lambda self, q: payload)
    prov = get_provider_by_platform("flipkart")
    if prov is not None:
        getattr(prov, "_cache", {}).clear()
    data = client.post(
        "/api/analyze",
        json={"url": f"https://www.flipkart.com/piludi-set/p/itmd4f87f61754c9?pid={pid}"},
    ).json()
    assert data["platform"] == "flipkart"
    assert data["data_status"] == "CATALOG_ONLY"
    assert data["product"]["title"]
    assert not data.get("reviews")
    assert data.get("product_trust_score") is None


def test_flipkart_sizechart_snippet_is_not_treated_as_a_review(monkeypatch):
    from src.providers.flipkart_provider import FlipkartProvider
    from src.providers.provider_factory import get_provider_by_platform

    monkeypatch.setattr("src.providers.flipkart_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_ID", "")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_TOKEN", "")
    pid = "SWDHGYBKHHHB47PM"
    payload = {
        "organic_results": [
            {
                "title": f"https://www.flipkart.com/rv/sizechart?pid={pid}",
                "link": f"https://www.flipkart.com/rv/sizechart?pid={pid}",
                "snippet": "No information is available for this page.",
            }
        ]
    }
    monkeypatch.setattr(FlipkartProvider, "_google_search", lambda self, q: payload)
    prov = get_provider_by_platform("flipkart")
    if prov is not None:
        getattr(prov, "_cache", {}).clear()
    data = client.post(
        "/api/analyze",
        json={"url": f"https://www.flipkart.com/piludi-embroidered-kurta-salwar-dupatta-set/p/itmd4f87f61754c9?pid={pid}"},
    ).json()
    assert data["platform"] == "flipkart"
    assert data["data_status"] == "CATALOG_ONLY"
    assert "sizechart" not in (data["product"]["title"] or "").lower()
    assert not data.get("reviews")
    assert data.get("product_trust_score") is None


def test_flipkart_truncated_pid_uses_affiliate_catalog(monkeypatch):
    from src.providers.flipkart_provider import FlipkartProvider, extract_flipkart_ids
    from src.providers.provider_factory import get_provider_by_platform

    pid = "CPRGGFSDKVBZEHJF"
    short = f"https://www.flipkart.com/product/p/item?pid={pid}"
    extracted_pid, item_id, title = extract_flipkart_ids(short)
    assert extracted_pid == pid
    assert item_id is None
    assert title is None

    payload = {
        "productBaseInfoV1": {
            "productId": pid,
            "title": "Glen SA4040BL Electric Vegetable & Fruit Chopper",
            "productBrand": "Glen",
            "categoryPath": "Home>Kitchen>Choppers",
            "productUrl": f"https://www.flipkart.com/glen-sa4040bl-electric-vegetable-fruit-chopper/p/itm644d01b88f3e4?pid={pid}",
            "flipkartSpecialPrice": {"amount": 999, "currency": "INR"},
            "imageUrls": {"400x400": "https://img.fkcdn.com/example.jpeg"},
            "averageRating": 4.1,
            "ratingCount": 4948,
        }
    }
    monkeypatch.setattr("src.providers.flipkart_provider.SERPAPI_API_KEY", "")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_ID", "aff-id")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_TOKEN", "aff-token")
    monkeypatch.setattr(FlipkartProvider, "_google_search", lambda self, q: None)

    def fake_affiliate(self, url, found_pid):
        from src.providers.flipkart_provider import parse_affiliate_product

        return parse_affiliate_product(payload, found_pid or pid, url)

    monkeypatch.setattr(FlipkartProvider, "_affiliate_product", fake_affiliate)
    prov = get_provider_by_platform("flipkart")
    if prov is not None:
        getattr(prov, "_cache", {}).clear()
    data = client.post("/api/analyze", json={"url": short}).json()
    assert data["platform"] == "flipkart"
    assert data["catalog_product_id"] == pid
    assert data["product"]["title"].startswith("Glen SA4040BL")
    assert data["product"]["brand"] == "Glen"
    assert data["product"]["price"] == 999
    assert data["product"]["currency"] == "INR"
    assert data["product"]["rating"] == 4.1
    assert not data.get("reviews")
    assert data.get("product_trust_score") is None
    assert data["data_status"] == "CATALOG_ONLY"
    assert "review text" in data["user_message"].lower()
    assert "aff-token" not in str(data)


def test_platforms_catalog_lists_review_credentials():
    data = client.get("/api/platforms").json()
    by_key = {row["platform"]: row for row in data["platforms"]}
    assert by_key["amazon"]["reviews_available_now"] is True
    assert "SERPAPI_API_KEY" in by_key["amazon"]["credential_required"]
    assert by_key["flipkart"]["catalog_available_now"] is True
    assert by_key["flipkart"]["reviews_available_now"] is False
    assert "FLIPKART_AFFILIATE_ID" in by_key["flipkart"]["credential_required"]
    assert by_key["myntra"]["reviews_available_now"] is False
    assert "MYNTRA_PROVIDER_API_KEY" in by_key["myntra"]["credential_required"]


def test_public_jsonld_reviews_run_trust_engine(monkeypatch):
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"Cotton Kurta Set","brand":{"@type":"Brand","name":"DemoBrand"},
     "offers":{"@type":"Offer","price":"799","priceCurrency":"INR"},
     "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.2","ratingCount":"12"},
     "review":[
       {"@type":"Review","reviewBody":"The fabric feels comfortable and stitching is neat for daily wear at home.","reviewRating":{"ratingValue":5},"author":{"name":"Asha"}},
       {"@type":"Review","reviewBody":"Colour is slightly different than the photo but overall the set is usable.","reviewRating":{"ratingValue":3},"author":{"name":"Ravi"}}
     ]}
    </script>
    </head></html>
    """
    monkeypatch.setattr("src.providers.page_metadata.fetch_public_html", lambda url: html)
    data = client.post(
        "/api/analyze",
        json={"url": "https://www.myntra.com/kurta-sets/cotton-kurta-set/123/buy"},
    ).json()
    assert data["platform"] == "myntra"
    assert data["data_status"] == "LIVE"
    assert data["product"]["title"] == "Cotton Kurta Set"
    assert data["product"]["price"] == 799
    assert data["product"]["currency"] == "INR"
    assert data["review_count"] == 2
    assert data["product_trust_score"] is not None
    assert data["sentiment_summary"]


def test_store_product_ids_extracted_from_real_url_shapes():
    from src.product_identity import extract_store_product_id

    assert extract_store_product_id("myntra", "https://www.myntra.com/kurtas/aurelia/aurelia-women-embroidered-kurta/33517605/buy") == "33517605"
    assert extract_store_product_id("nykaa", "https://www.nykaa.com/m-a-c-powder-kiss-lipstick/p/377929") == "377929"
    assert extract_store_product_id("croma", "https://www.croma.com/honeywell-suono-p300/p/311779") == "311779"
    assert extract_store_product_id("meesho", "https://www.meesho.com/pretty-kurti/p/1k2abc") == "1k2abc"
    assert extract_store_product_id("ajio", "https://www.ajio.com/brand-kurta/p/410376049_navy") == "410376049_navy"
    assert extract_store_product_id("reliance_digital", "https://www.reliancedigital.in/iphone/p/490123") == "490123"
    assert extract_store_product_id(
        "reliance_digital",
        "https://www.reliancedigital.in/product/samsung-400-w-soundbar-hw-b750fxl-black-mc8rpk-9267083",
    ) == "9267083"
    assert extract_store_product_id("tata_cliq", "https://www.tatacliq.com/product/p-mp0000000123") == "mp0000000123"
    assert extract_store_product_id("flipkart", "https://www.flipkart.com/x/p/itmabc?pid=SWDHGYBKHHHB47PM") == "SWDHGYBKHHHB47PM"


def test_flipkart_share_link_is_not_a_listing():
    from src.platform_detector import detect_platform

    d = detect_platform("https://dl.flipkart.com/s/oAztvHuuuN")
    assert d.platform == "flipkart"
    assert d.listing_page is False


def test_flipkart_share_link_resolves_pid_from_location(monkeypatch):
    from src.providers import flipkart_provider as fp

    class FakeResp:
        status_code = 301
        headers = {
            "location": "https://dl.flipkart.com/dl/saini-creation-women-kurti-pant-set/p/itm9a62206ed2615?pid=ETHHG885FFFZYFSN"
        }
        url = "https://dl.flipkart.com/s/oAztvHuuuN"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return FakeResp()

    fp._SHARE_CACHE.clear()
    monkeypatch.setattr(fp.httpx, "Client", FakeClient)
    resolved = fp.resolve_flipkart_product_url("https://dl.flipkart.com/s/oAztvHuuuN")
    pid, item_id, title = fp.extract_flipkart_ids(resolved)
    assert pid == "ETHHG885FFFZYFSN"
    assert item_id == "itm9a62206ed2615"
    assert title


def test_flipkart_share_link_analyze_uses_resolved_pid(monkeypatch):
    from src.providers.flipkart_provider import FlipkartProvider
    from src.providers.provider_factory import get_provider_by_platform
    from src.providers import flipkart_provider as fp

    monkeypatch.setattr("src.providers.flipkart_provider.SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_ID", "")
    monkeypatch.setattr("src.providers.flipkart_provider.FLIPKART_AFFILIATE_TOKEN", "")
    pid = "ETHHG885FFFZYFSN"
    resolved = f"https://www.flipkart.com/saini-creation-women-kurti-pant-set/p/itm9a62206ed2615?pid={pid}"
    monkeypatch.setattr(fp, "resolve_flipkart_product_url", lambda url: resolved)
    monkeypatch.setattr("src.pipeline.resolve_flipkart_product_url", lambda url: resolved)
    payload = {
        "organic_results": [
            {
                "title": "SAINI CREATION Women Kurti Pant Set",
                "link": f"https://www.flipkart.com/saini-creation-women-kurti-pant-set/p/itm9a62206ed2615?pid={pid}",
                "snippet": "Stitching is neat and the fabric feels comfortable for daily wear at home.",
                "rich_snippet": {
                    "top": {
                        "detected_extensions": {
                            "rating": 4.1,
                            "reviews": 88,
                            "price_from": 499,
                            "currency": "₹",
                        }
                    }
                },
            }
        ]
    }
    monkeypatch.setattr(FlipkartProvider, "_google_search", lambda self, q: payload)
    prov = get_provider_by_platform("flipkart")
    if prov is not None:
        getattr(prov, "_cache", {}).clear()
    data = client.post("/api/analyze", json={"url": "https://dl.flipkart.com/s/oAztvHuuuN"}).json()
    assert data["platform"] == "flipkart"
    assert data["data_status"] == "LIVE"
    assert data["catalog_product_id"] == pid
    assert data["product"]["title"]
    assert data["product"]["price"] == 499
    assert data["review_count"] >= 1
    assert data["product_trust_score"] is not None


def test_each_supported_platform_jsonld_catalog_without_invented_reviews(monkeypatch):
    samples = [
        ("https://www.myntra.com/kurtas/brand/name/33517605/buy", "myntra", "33517605", "Myntra Sample Kurta"),
        ("https://www.nykaa.com/lipstick/p/377929", "nykaa", "377929", "Nykaa Sample Lipstick"),
        ("https://www.meesho.com/pretty-kurti/p/2kp2tz", "meesho", "2kp2tz", "Meesho Sample Kurti"),
        ("https://www.ajio.com/brand-kurta/p/466686023_multi", "ajio", "466686023_multi", "Ajio Sample Kurta"),
        ("https://www.croma.com/speaker/p/311779", "croma", "311779", "Croma Sample Speaker"),
        ("https://www.reliancedigital.in/product/samsung-soundbar-mc8rpk-9267083", "reliance_digital", "9267083", "Reliance Sample Soundbar"),
        ("https://www.tatacliq.com/kurti/p-mp000000023378190", "tata_cliq", "mp000000023378190", "Tata CLiQ Sample Kurti"),
    ]
    for url, platform, pid, title in samples:
        html = f"""
        <html><head>
        <script type="application/ld+json">
        {{"@type":"Product","name":"{title}","sku":"{pid}","brand":{{"@type":"Brand","name":"SampleBrand"}},
         "offers":{{"@type":"Offer","price":"999","priceCurrency":"INR"}},
         "aggregateRating":{{"@type":"AggregateRating","ratingValue":"4.1","ratingCount":"8"}}}}
        </script>
        </head></html>
        """
        monkeypatch.setattr("src.providers.page_metadata.fetch_public_html", lambda u, h=html: h)
        from src.providers.page_metadata import clear_catalog_caches

        clear_catalog_caches()
        data = client.post("/api/analyze", json={"url": url}).json()
        assert data["platform"] == platform, url
        assert data["data_status"] == "CATALOG_ONLY", url
        assert data["product"]["title"] == title
        assert data["product"]["price"] == 999
        assert data["product"]["currency"] == "INR"
        assert data["product"]["brand"] == "SampleBrand"
        assert data["catalog_product_id"] == pid
        assert data["product_trust_score"] is None
        assert not data.get("reviews")


def test_category_pages_are_invalid_product_urls_not_unsupported():
    listing = [
        ("https://www.meesho.com/women-kurtis/pl/3j0", "meesho"),
        ("https://www.ajio.com/find/Green-Kurta", "ajio"),
        ("https://www.reliancedigital.in/c/audio", "reliance_digital"),
    ]
    for url, platform in listing:
        data = client.post("/api/analyze", json={"url": url}).json()
        assert data["platform"] == platform
        assert data["data_status"] == "INVALID_URL"
        assert data["product_trust_score"] is None


def test_myntra_google_catalog_fills_price_without_inventing_reviews(monkeypatch):
    from src.schemas import Product

    url = "https://www.myntra.com/kurtas/aurelia/aurelia-women-embroidered-kurta/33517605/buy"
    product = Product(
        platform="myntra",
        product_id="33517605",
        url=url,
        title="AURELIA Women Embroidered Kurta",
        price=1299,
        currency="INR",
        rating=4.3,
        review_count=210,
        extra={"catalog_source": "serpapi_google"},
    )
    monkeypatch.setattr("src.providers.page_metadata.google_catalog", lambda u, p: (product, []))
    data = client.post("/api/analyze", json={"url": url}).json()
    assert data["platform"] == "myntra"
    assert data["data_status"] == "CATALOG_ONLY"
    assert data["product"]["title"] == "AURELIA Women Embroidered Kurta"
    assert data["product"]["price"] == 1299
    assert data["product"]["currency"] == "INR"
    assert data["catalog_product_id"] == "33517605"
    assert data["product_trust_score"] is None
    assert not data.get("reviews")


def test_other_platforms_google_review_snippets_enable_trust_analysis(monkeypatch):
    from src.schemas import Product, Review

    samples = [
        ("https://www.myntra.com/kurtas/aurelia/name/33517605/buy", "myntra", "33517605", "AURELIA Women Embroidered Kurta"),
        ("https://www.nykaa.com/lipstick/p/377929", "nykaa", "377929", "M.A.C Powder Kiss Lipstick"),
        ("https://www.meesho.com/pretty-kurti/p/2kp2tz", "meesho", "2kp2tz", "Pretty Cotton Kurti"),
        ("https://www.ajio.com/brand-kurta/p/466686023_multi", "ajio", "466686023_multi", "KISAH Men Printed Kurta"),
        ("https://www.croma.com/speaker/p/311779", "croma", "311779", "Honeywell Bluetooth Speaker"),
        ("https://www.reliancedigital.in/product/samsung-soundbar-7536837", "reliance_digital", "7536837", "Samsung Soundbar"),
        ("https://www.tatacliq.com/kurti/p-mp000000023378190", "tata_cliq", "mp000000023378190", "Zuri Sky Blue Kurti"),
    ]
    for url, platform, pid, title in samples:
        product = Product(
            platform=platform,
            product_id=pid,
            url=url,
            title=title,
            brand="SampleBrand",
            price=799,
            currency="INR",
            rating=4.2,
            review_count=40,
            extra={"catalog_source": "serpapi_google"},
        )
        reviews = [
            Review(
                platform=platform,
                product_id=pid,
                review_id=f"{pid}-g1",
                text="I bought this last week and the quality is good for daily wear at home.",
            )
        ]
        monkeypatch.setattr(
            "src.providers.page_metadata.google_catalog",
            lambda u, p, prod=product, revs=reviews: (prod, revs),
        )
        from src.providers.page_metadata import clear_catalog_caches

        clear_catalog_caches()
        data = client.post("/api/analyze", json={"url": url}).json()
        assert data["platform"] == platform, url
        assert data["data_status"] == "LIVE", url
        assert data["product"]["title"] == title
        assert data["product"]["price"] == 799
        assert data["review_count"] >= 1
        assert data["product_trust_score"] is not None


def test_nykaa_uses_google_review_snippets_when_catalog_already_complete(monkeypatch):
    from src.schemas import Review

    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"Kiehl's Ultra Facial Cream","brand":{"@type":"Brand","name":"Kiehl's"},
     "image":"https://images.example.com/cream.jpg",
     "offers":{"@type":"Offer","price":"1650","priceCurrency":"INR"},
     "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.5","ratingCount":"4206"}}
    </script>
    </head></html>
    """
    monkeypatch.setattr("src.providers.page_metadata.fetch_public_html", lambda url: html)
    monkeypatch.setattr(
        "src.providers.page_metadata.google_reviews",
        lambda url, platform: [
            Review(
                platform="nykaa",
                product_id="435017",
                review_id="435017-g1",
                text="Kiehls ultra facial cream has a smooth formulation and this suits my dry skin very well.",
            )
        ],
    )
    from src.providers.page_metadata import clear_catalog_caches

    clear_catalog_caches()
    data = client.post(
        "/api/analyze",
        json={"url": "https://www.nykaa.com/kiehl-s-ultra-facial-cream/p/435017"},
    ).json()
    assert data["platform"] == "nykaa"
    assert data["data_status"] == "LIVE"
    assert data["product"]["title"] == "Kiehl's Ultra Facial Cream"
    assert data["product"]["price"] == 1650
    assert data["review_count"] >= 1
    assert data["product_trust_score"] is not None


def test_reviewer_section_requires_ids():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "!s.available || !rows.length" in js
    assert "filter((r) => r && r.reviewer_id)" in js




