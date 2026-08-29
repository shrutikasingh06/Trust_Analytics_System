from __future__ import annotations

from abc import ABC, abstractmethod

from src.schemas import Product, Review


class BaseProvider(ABC):
    """Authorized data access only. Never invent product or review fields."""

    name: str
    platform: str
    display_name: str = ""

    def source_label(self) -> str:
        """Human-readable data source for the UI. Override in live providers if a backend is active."""
        return self.display_name or self.name

    @abstractmethod
    def supports_url(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def fetch_product(self, url: str) -> Product | None:
        """Return None if the provider cannot retrieve a product. Do not fabricate."""
        raise NotImplementedError

    @abstractmethod
    def fetch_reviews(self, url: str, limit: int | None = None) -> list[Review]:
        """Return an empty list if reviews are unavailable. Do not fabricate."""
        raise NotImplementedError

    def configuration_help(self) -> str:
        return (
            "Configure an authorized data provider/API for this platform "
            "via environment variables. See .env.example."
        )
