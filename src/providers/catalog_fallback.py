from src.config import MAX_REVIEWS
from src.platform_detector import detect_platform
from src.providers.page_metadata import catalog_from_url
from src.providers.unconfigured import UnconfiguredPlatformProvider
from src.schemas import Product, Review


class CatalogFallbackProvider(UnconfiguredPlatformProvider):
    """Detected storefront: public page metadata, never invented reviews."""

    def is_configured(self) -> bool:
        return True

    def health_check(self) -> dict:
        info = super().health_check()
        info["configured"] = True
        info["catalog_fallback"] = "public_page_jsonld_open_graph"
        info["reason"] = (
            f"{self.display_name} has no official review API. "
            "Product fields come from public page metadata or the existing SerpAPI Google catalog lookup."
        )
        return info

    def fetch_product(self, url: str) -> Product | None:
        product, _ = catalog_from_url(url, self.platform, use_google=True)
        return product

    def fetch_reviews(self, url: str, limit: int | None = None) -> list[Review]:
        _, reviews = catalog_from_url(url, self.platform, use_google=True)
        cap = limit or MAX_REVIEWS
        return reviews[:cap]
