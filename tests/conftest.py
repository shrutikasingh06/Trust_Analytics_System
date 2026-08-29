import pytest


@pytest.fixture(autouse=True)
def disable_public_network(monkeypatch):
    """Unit tests must not hit live storefronts or SerpAPI. The website process still does."""
    from src.providers.page_metadata import clear_catalog_caches
    from src.providers.serp_catalog import _CACHE as serp_cache

    clear_catalog_caches()
    serp_cache.clear()
    monkeypatch.setattr("src.providers.page_metadata.fetch_public_html", lambda url: None)
    monkeypatch.setattr("src.providers.serp_catalog.google_catalog", lambda url, platform: (None, []))
    monkeypatch.setattr("src.providers.page_metadata.google_catalog", lambda url, platform: (None, []))
    monkeypatch.setattr("src.providers.serp_catalog.google_reviews", lambda url, platform: [])
    monkeypatch.setattr("src.providers.page_metadata.google_reviews", lambda url, platform: [])
