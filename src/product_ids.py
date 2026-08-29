"""Extract Amazon ASINs from Amazon URLs only. No cross-store identifier mapping."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

ASIN_TOKEN = re.compile(r"^[A-Z0-9]{10}$", re.I)
PATH_ASIN_RE = re.compile(
    r"(?:/dp/|/gp/product/|/gp/aw/d/|/product-reviews/|/exec/obidos/ASIN/)([A-Z0-9]{10})(?:[/?#]|$)",
    re.I,
)
_QUERY_ASIN_KEYS = {"asin", "pd_rd_i", "creativeasin"}


def looks_like_catalog_id(value: str | None) -> bool:
    if not value:
        return False
    return bool(ASIN_TOKEN.fullmatch(value.strip()))


def amazon_domain_from_url(url: str) -> str:
    """SerpAPI/Rainforest amazon_domain: amazon.com, amazon.in, amazon.co.uk, …"""
    host = urlparse(url if "://" in (url or "") else f"https://{url or ''}").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    if host.startswith("amazon.") or host.endswith(".amazon.com"):
        return host if host.startswith("amazon.") else "amazon.com"
    return host or "amazon.com"


def extract_asin(url: str) -> str | None:
    """ASIN from /dp/, /gp/product/, query asin/pd_rd_i, or Amazon hub `s=` when it is an ASIN."""
    text = (url or "").strip()
    if not text:
        return None
    m = PATH_ASIN_RE.search(text)
    if m:
        return m.group(1).upper()
    parsed = urlparse(text if "://" in text else f"https://{text}")
    path = (parsed.path or "/").lower().rstrip("/") or "/"
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if looks_like_catalog_id(value) and key.lower() in _QUERY_ASIN_KEYS:
            return value.strip().upper()
    if path != "/s" and not path.startswith("/s/"):
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() == "s" and looks_like_catalog_id(value):
                return value.strip().upper()
    m = re.search(r"[?&]asin=([A-Z0-9]{10})(?:[&#]|$)", text, re.I)
    if m:
        return m.group(1).upper()
    return None


def canonical_amazon_product_url(url: str) -> str | None:
    asin = extract_asin(url)
    if not asin:
        return None
    domain = amazon_domain_from_url(url)
    return f"https://www.{domain}/dp/{asin}"


def extract_catalog_product_id(platform: str, url: str, product_id: str | None = None) -> str | None:
    """Return an Amazon ASIN only when the detected platform is Amazon.

    Non-Amazon store IDs are never treated as ASINs, even if they are 10 characters.
    """
    if platform != "amazon":
        return None
    found = extract_asin(url or "")
    if found:
        return found.upper()
    if looks_like_catalog_id(product_id):
        return product_id.strip().upper()
    return None
