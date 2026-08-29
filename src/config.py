"""Platform/provider configuration from environment. A listed platform is not LIVE until its provider is configured and fetch succeeds."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SUPPORTED_PLATFORMS: dict[str, bool] = {
    "amazon": True,
    "flipkart": True,
    "myntra": True,
    "nykaa": True,
    "meesho": True,
    "ajio": True,
    "croma": True,
    "reliance_digital": True,
    "tata_cliq": True,
}

PLATFORM_LABELS = {
    "amazon": "Amazon",
    "flipkart": "Flipkart",
    "myntra": "Myntra",
    "nykaa": "Nykaa",
    "meesho": "Meesho",
    "ajio": "Ajio",
    "croma": "Croma",
    "reliance_digital": "Reliance Digital",
    "tata_cliq": "Tata CLiQ",
    "generic": "Generic / other",
    "unsupported": "Unsupported",
}

# Authorized third-party / official API keys. Empty = provider not configured.
# AMAZON_PROVIDER_API_KEY is an alias for Rainforest (the review-capable Amazon backend).
RAINFOREST_API_KEY = (
    os.getenv("RAINFOREST_API_KEY", "").strip()
    or os.getenv("AMAZON_PROVIDER_API_KEY", "").strip()
)
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()
AMAZON_PAAPI_ACCESS_KEY = os.getenv("AMAZON_PAAPI_ACCESS_KEY", "").strip()
AMAZON_PAAPI_SECRET_KEY = os.getenv("AMAZON_PAAPI_SECRET_KEY", "").strip()
AMAZON_PAAPI_PARTNER_TAG = os.getenv("AMAZON_PAAPI_PARTNER_TAG", "").strip()
FLIPKART_AFFILIATE_ID = os.getenv("FLIPKART_AFFILIATE_ID", "").strip()
FLIPKART_AFFILIATE_TOKEN = (
    os.getenv("FLIPKART_AFFILIATE_TOKEN", "").strip()
    or os.getenv("FLIPKART_PROVIDER_API_KEY", "").strip()
)
GENERIC_JSONLD_ENABLED = os.getenv("GENERIC_JSONLD_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}

HTTP_TIMEOUT_SEC = float(os.getenv("HTTP_TIMEOUT_SEC", "20"))
MAX_REVIEWS = int(os.getenv("MAX_REVIEWS", "50"))
HISTORICAL_REVIEW_LIMIT = int(os.getenv("HISTORICAL_REVIEW_LIMIT", "200"))
HISTORICAL_REVIEW_LIMIT = int(os.getenv("HISTORICAL_REVIEW_LIMIT", "200"))


def amazon_provider_configured() -> bool:
    return bool(RAINFOREST_API_KEY or SERPAPI_API_KEY)


def flipkart_provider_configured() -> bool:
    return bool(SERPAPI_API_KEY or (FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN))
