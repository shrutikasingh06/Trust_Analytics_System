"""
Optional last-resort access for sites that publish public JSON-LD Product markup.

Disabled by default (GENERIC_JSONLD_ENABLED=false). This is not an anti-bot bypass:
it only performs a single HTTP GET and reads application/ld+json if present.
It never solves CAPTCHA, never logs in, and never claims reviews that are absent.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from src.config import GENERIC_JSONLD_ENABLED, HTTP_TIMEOUT_SEC
from src.providers.base_provider import BaseProvider
from src.schemas import Product, Review

log = logging.getLogger("trust_analytics.providers.generic")

LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


class GenericProvider(BaseProvider):
    name = "generic_jsonld"
    platform = "generic"
    display_name = "Generic JSON-LD"

    def supports_url(self, url: str) -> bool:
        return GENERIC_JSONLD_ENABLED and url.startswith(("http://", "https://"))

    def is_configured(self) -> bool:
        return GENERIC_JSONLD_ENABLED

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "configured": self.is_configured(),
            "note": "JSON-LD Product only; reviews almost never present. Disabled by default.",
        }

    def fetch_product(self, url: str) -> Product | None:
        if not self.is_configured():
            return None
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SEC, follow_redirects=True) as client:
                r = client.get(url, headers={"User-Agent": "TrustAnalyticsAcademicResearch/1.0"})
                if r.status_code >= 400:
                    return None
                html = r.text
        except Exception as exc:
            log.info("Generic JSON-LD fetch failed: %s", exc)
            return None
        for block in LD_RE.findall(html):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            product = _product_from_ld(data, url)
            if product:
                return product
        return None

    def fetch_reviews(self, url: str, limit: int | None = None) -> list[Review]:
        return []


def _product_from_ld(data, url: str) -> Product | None:
    nodes = data if isinstance(data, list) else [data]
    graph = []
    for node in nodes:
        if isinstance(node, dict) and "@graph" in node:
            graph.extend(node["@graph"] if isinstance(node["@graph"], list) else [node["@graph"]])
        elif isinstance(node, dict):
            graph.append(node)
    for node in graph:
        types = node.get("@type")
        types_l = types if isinstance(types, list) else [types]
        if "Product" not in types_l:
            continue
        offers = node.get("offers") or {}
        if isinstance(offers, list) and offers:
            offers = offers[0]
        agg = node.get("aggregateRating") or {}
        image = node.get("image")
        if isinstance(image, list) and image:
            image = image[0]
        if isinstance(image, dict):
            image = image.get("url")
        brand = node.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        return Product(
            platform="generic",
            product_id=node.get("sku") or node.get("productID"),
            url=node.get("url") or url,
            title=node.get("name"),
            brand=brand if isinstance(brand, str) else None,
            category=node.get("category") if isinstance(node.get("category"), str) else None,
            price=_safe_float(offers.get("price") if isinstance(offers, dict) else None),
            currency=offers.get("priceCurrency") if isinstance(offers, dict) else None,
            rating=_safe_float(agg.get("ratingValue") if isinstance(agg, dict) else None),
            rating_count=_safe_int(agg.get("ratingCount") if isinstance(agg, dict) else None),
            review_count=_safe_int(agg.get("reviewCount") if isinstance(agg, dict) else None),
            image_url=image if isinstance(image, str) else None,
        )
    return None


def _safe_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
