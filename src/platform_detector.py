"""Detect e-commerce platform from a product URL. Does not fetch any data."""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.config import PLATFORM_LABELS, SUPPORTED_PLATFORMS
from src.product_ids import canonical_amazon_product_url, extract_asin

HOST_MAP: list[tuple[str, str]] = [
    ("amazon.", "amazon"),
    ("amzn.to", "amazon"),
    ("amzn.in", "amazon"),
    ("flipkart.com", "flipkart"),
    ("dl.flipkart.com", "flipkart"),
    ("myntra.com", "myntra"),
    ("nykaa.com", "nykaa"),
    ("nykaafashion.com", "nykaa"),
    ("meesho.com", "meesho"),
    ("ajio.com", "ajio"),
    ("croma.com", "croma"),
    ("reliancedigital.in", "reliance_digital"),
    ("tatacliq.com", "tata_cliq"),
]


@dataclass(frozen=True)
class Detection:
    platform: str
    platform_label: str
    enabled: bool
    host: str | None
    valid_url: bool
    reason: str | None = None
    listing_page: bool = False
    normalized_url: str | None = None


_TRACKING_KEYS = {
    "ref",
    "ref_",
    "tag",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ie",
    "qid",
    "sr",
    "sprefix",
    "crid",
    "keywords",
    "linkcode",
    "linkid",
    "camp",
    "creative",
    "creativeasin",
    "ascsubtag",
}
_TRACKING_PREFIXES = ("utm_", "pd_rd_", "pf_rd_", "ns_", "spia")

_NON_PRODUCT_PATHS = (
    "/cart",
    "/gp/cart",
    "/checkout",
    "/gp/buy",
    "/ap/signin",
    "/login",
    "/account",
    "/gp/css",
    "/wishlist",
    "/gp/registry",
    "/gp/your-account",
    "/seller",
    "/gp/help",
    "/gp/bestsellers",
    "/gp/new-releases",
    "/gp/offer-listing",
    "/gp/aag",
)


def normalize_url(raw: str) -> str | None:
    """Strip whitespace, fragments, and tracking query params. Accept http(s)/www."""
    text = (raw or "").replace("\n", "").replace("\r", "").strip()
    if not text:
        return None
    if " " in text and not text.startswith(("http://", "https://")):
        return None
    if not text.startswith(("http://", "https://")):
        text = "https://" + text.lstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.strip()
    if not host or "." not in host:
        return None
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lk = key.lower()
        if lk in _TRACKING_KEYS or any(lk.startswith(p) for p in _TRACKING_PREFIXES):
            continue
        kept.append((key, value))
    query = urlencode(kept, doseq=True)
    return urlunparse((parsed.scheme, host, parsed.path or "/", parsed.params, query, ""))


def is_listing_or_search_url(url: str) -> bool:
    """True for search/category/home/cart/login pages — not a product-detail URL."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "/").lower().rstrip("/") or "/"
    query = (parsed.query or "").lower()
    if "flipkart.com" in host and re.search(r"/s/[a-z0-9]{5,}$", path):
        return False
    if path == "/":
        return True
    if any(path == p or path.startswith(p + "/") for p in _NON_PRODUCT_PATHS):
        return True
    # Amazon search is /s or /s/ref=… — not category slugs like /sarees/…
    if path == "/s" or path.startswith("/s/ref"):
        return True
    if path == "/search" or path.startswith("/search/"):
        return True
    if path.startswith("/gp/search") or path.startswith("/gp/bestsellers") or path.startswith("/gp/new-releases"):
        return True
    if path == "/b" or path.startswith("/b/") or path == "/stores" or path.startswith("/stores/"):
        return True
    if path == "/mh" or path.startswith("/mh/"):
        return True
    if path.startswith("/catalog/search"):
        return True
    if "/clp/" in path or "/pl/" in path or "/find/" in path:
        return True
    if re.search(r"/s/[a-z0-9-]+-\d+", path):
        return True
    if path.startswith("/c/") or path.startswith("/collection/"):
        return True
    if ("k=" in query or "field-keywords=" in query) and "/dp/" not in path and "/p/" not in path:
        return True
    return False


def detect_platform(url: str) -> Detection:
    normalized = normalize_url(url)
    if not normalized:
        return Detection(
            platform="unsupported",
            platform_label=PLATFORM_LABELS["unsupported"],
            enabled=False,
            host=None,
            valid_url=False,
            reason="The input is not a valid http(s) URL.",
            listing_page=False,
            normalized_url=None,
        )
    host = urlparse(normalized).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    platform = "unsupported"
    for fragment, name in HOST_MAP:
        if fragment in host:
            platform = name
            break
    if platform == "unsupported":
        return Detection(
            platform="unsupported",
            platform_label=PLATFORM_LABELS["unsupported"],
            enabled=False,
            host=host,
            valid_url=True,
            reason="Host is not in the supported platform list.",
            listing_page=False,
            normalized_url=normalized,
        )
    enabled = bool(SUPPORTED_PLATFORMS.get(platform, False))
    listing = is_listing_or_search_url(normalized)
    amazon_asin = (
        (extract_asin(url) or extract_asin(normalized)) if platform == "amazon" else None
    )
    if platform == "amazon" and amazon_asin:
        path = (urlparse(normalized).path or "/").lower()
        forced_non_product = any(path == p or path.startswith(p + "/") for p in _NON_PRODUCT_PATHS)
        if not forced_non_product:
            listing = False
            canonical = canonical_amazon_product_url(url) or canonical_amazon_product_url(normalized)
            if canonical:
                normalized = canonical
    return Detection(
        platform=platform,
        platform_label=PLATFORM_LABELS.get(platform, platform),
        enabled=enabled,
        host=host,
        valid_url=True,
        reason=(
            "This looks like a search or category page, not a product page."
            if listing
            else (None if enabled else "Platform is listed but disabled in configuration.")
        ),
        listing_page=listing,
        normalized_url=normalized,
    )
