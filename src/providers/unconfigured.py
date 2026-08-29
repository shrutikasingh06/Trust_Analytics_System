"""Platform registered in the factory but without an authorized backend."""
from __future__ import annotations

from src.platform_detector import detect_platform
from src.providers.base_provider import BaseProvider
from src.providers.platform_requirements import for_platform
from src.schemas import Product, Review


class UnconfiguredPlatformProvider(BaseProvider):
    def __init__(self, platform: str, display_name: str):
        self.platform = platform
        self.name = platform
        self.display_name = display_name

    def supports_url(self, url: str) -> bool:
        return detect_platform(url).platform == self.platform

    def is_configured(self) -> bool:
        return False

    def health_check(self) -> dict:
        req = for_platform(self.platform)
        return {
            "provider": self.name,
            "configured": False,
            "reason": f"No authorized {self.display_name} product/review API is configured.",
            **req,
        }

    def configuration_help(self) -> str:
        req = for_platform(self.platform)
        cred = req.get("credential_required") or "an official partner API key"
        return (
            f"{self.display_name} has no public catalog or review API to call. "
            f"Required for review analysis: {cred}. "
            f"Where: {req.get('where_to_get_it', 'no public signup')}."
        )

    def fetch_product(self, url: str) -> Product | None:
        return None

    def fetch_reviews(self, url: str, limit: int | None = None) -> list[Review]:
        return []
