"""Select a provider from a detected platform. Adding a platform = new module + one dict entry."""
from __future__ import annotations

from src.config import PLATFORM_LABELS, SUPPORTED_PLATFORMS
from src.platform_detector import Detection, detect_platform
from src.providers.ajio_provider import AjioProvider
from src.providers.amazon_provider import AmazonProvider
from src.providers.base_provider import BaseProvider
from src.providers.croma_provider import CromaProvider
from src.providers.flipkart_provider import FlipkartProvider
from src.providers.generic_provider import GenericProvider
from src.providers.meesho_provider import MeeshoProvider
from src.providers.myntra_provider import MyntraProvider
from src.providers.nykaa_provider import NykaaProvider
from src.providers.reliance_digital_provider import RelianceDigitalProvider
from src.providers.platform_requirements import for_platform
from src.providers.tata_cliq_provider import TataCliqProvider

_PROVIDERS: dict[str, BaseProvider] = {
    "amazon": AmazonProvider(),
    "flipkart": FlipkartProvider(),
    "myntra": MyntraProvider(),
    "nykaa": NykaaProvider(),
    "meesho": MeeshoProvider(),
    "ajio": AjioProvider(),
    "croma": CromaProvider(),
    "reliance_digital": RelianceDigitalProvider(),
    "tata_cliq": TataCliqProvider(),
}

_GENERIC = GenericProvider()


def list_providers() -> dict[str, dict]:
    out = {}
    for key, p in _PROVIDERS.items():
        health = p.health_check()
        health["display_name"] = p.display_name or PLATFORM_LABELS.get(key, key)
        health["enabled_in_config"] = bool(SUPPORTED_PLATFORMS.get(key, False))
        for field, value in for_platform(key).items():
            health.setdefault(field, value)
        out[key] = health
    out["generic"] = _GENERIC.health_check()
    return out


def get_provider_by_platform(platform: str) -> BaseProvider | None:
    return _PROVIDERS.get(platform)


def get_provider(url: str) -> tuple[Detection, BaseProvider | None]:
    detection = detect_platform(url)
    if not detection.valid_url or detection.platform == "unsupported" or not detection.enabled:
        return detection, None
    return detection, get_provider_by_platform(detection.platform)
