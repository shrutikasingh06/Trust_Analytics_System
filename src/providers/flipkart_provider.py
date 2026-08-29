"""Flipkart product/review access via authorized APIs only (affiliate catalog and/or SerpAPI Google)."""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urljoin, urlparse

import httpx

from src.config import (
    FLIPKART_AFFILIATE_ID,
    FLIPKART_AFFILIATE_TOKEN,
    HTTP_TIMEOUT_SEC,
    MAX_REVIEWS,
    SERPAPI_API_KEY,
)
from src.platform_detector import detect_platform
from src.providers.base_provider import BaseProvider
from src.providers.platform_requirements import for_platform
from src.schemas import Product, Review

log = logging.getLogger("trust_analytics.providers.flipkart")
logging.getLogger("httpx").setLevel(logging.WARNING)

PID_QUERY_RE = re.compile(r"[?&]pid=([A-Z0-9]+)", re.I)
ITEM_RE = re.compile(r"/p/(itm[0-9a-z]{10,})", re.I)
_GENERIC_SLUGS = {"product", "p", "item", "itme", "dl", "rv"}
_AFFILIATE_ENDPOINTS = (
    "https://affiliate-api.flipkart.net/affiliate/1.0/product.json",
    "https://affiliate-api.flipkart.net/affiliate/product/json",
)
_CACHE_MAX = 24
_DEAL_MARKERS = ("% off", "₹", "rs.", "hot deal", "free delivery", "in stock")
_JUNK_SNIPPETS = (
    "no information is available",
    "page not found",
    "something went wrong",
    "access denied",
)
_NON_PRODUCT_PATHS = ("/rv/", "sizechart", "/search", "/login", "/account", "/help/")
_SHARE_PATH = re.compile(r"/s/([A-Za-z0-9]{5,})/?$", re.I)
_SHARE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
_SHARE_CACHE: dict[str, str] = {}
_SHARE_CACHE_MAX = 40


def extract_flipkart_ids(url: str) -> tuple[str | None, str | None, str | None]:
    """Return (pid, item_id, title_guess) from a Flipkart product URL."""
    text = url or ""
    pid = None
    m = PID_QUERY_RE.search(text)
    if m:
        pid = m.group(1).upper()
    item_id = None
    im = ITEM_RE.search(text)
    if im:
        item_id = im.group(1).lower()
    title = None
    path = urlparse(text if "://" in text else f"https://{text}").path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[-2] == "p":
        slug = parts[-3] if len(parts) >= 3 else parts[0]
        if slug and slug.lower() not in _GENERIC_SLUGS:
            title = slug.replace("-", " ").strip().title() or None
    return pid, item_id, title


def _product_slug(url: str) -> str | None:
    path = urlparse(url if "://" in (url or "") else f"https://{url}").path
    parts = [p for p in path.split("/") if p]
    if "p" in parts:
        idx = parts.index("p")
        if idx >= 1:
            slug = parts[idx - 1].lower()
            if slug not in _GENERIC_SLUGS and len(slug) > 4:
                return slug
    if "product-reviews" in parts:
        idx = parts.index("product-reviews")
        if idx >= 1:
            slug = parts[idx - 1].lower()
            if slug not in _GENERIC_SLUGS and len(slug) > 4:
                return slug
    return None


def looks_like_flipkart_pid(pid: str | None) -> bool:
    if not pid:
        return False
    return 14 <= len(pid) <= 18 and pid.isalnum()


def is_flipkart_share_url(url: str) -> bool:
    parsed = urlparse(url if "://" in (url or "") else f"https://{url}")
    host = (parsed.netloc or "").lower()
    if "flipkart.com" not in host:
        return False
    pid, item_id, _ = extract_flipkart_ids(url)
    if pid or item_id:
        return False
    return bool(_SHARE_PATH.search(parsed.path or ""))


def resolve_flipkart_product_url(url: str) -> str:
    """Turn dl.flipkart.com/s/… share links into a product URL using HTTP Location only.

    Does not load the product HTML, does not solve CAPTCHA, and does not invent a pid.
    """
    current = (url or "").strip()
    if not current:
        return current
    cached = _SHARE_CACHE.get(current)
    if cached:
        return cached
    original = current
    candidates = [current]
    parsed = urlparse(current if "://" in current else f"https://{current}")
    code_match = _SHARE_PATH.search(parsed.path or "")
    if code_match:
        code = code_match.group(1)
        for host in ("https://dl.flipkart.com/s/", "https://www.flipkart.com/s/"):
            alt = host + code
            if alt not in candidates:
                candidates.append(alt)
    resolved = current
    for start in candidates:
        hop = start
        seen: set[str] = set()
        for _ in range(4):
            pid, item_id, _ = extract_flipkart_ids(hop)
            if looks_like_flipkart_pid(pid) or item_id:
                resolved = hop
                break
            key = hop.lower()
            if key in seen:
                break
            seen.add(key)
            nxt = _share_location(hop)
            if not nxt or nxt.lower() == key:
                break
            hop = nxt
        pid, item_id, _ = extract_flipkart_ids(hop)
        if looks_like_flipkart_pid(pid) or item_id:
            resolved = hop
            break
    pid, item_id, _ = extract_flipkart_ids(resolved)
    if looks_like_flipkart_pid(pid) or item_id:
        if len(_SHARE_CACHE) >= _SHARE_CACHE_MAX:
            _SHARE_CACHE.clear()
        _SHARE_CACHE[original] = resolved
    return resolved


def _share_location(url: str) -> str | None:
    headers = {
        "User-Agent": _SHARE_HEADERS["User-Agent"],
        "Accept": "text/html",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    last_loc = None
    for attempt in range(3):
        try:
            with httpx.Client(
                timeout=min(HTTP_TIMEOUT_SEC, 12.0),
                follow_redirects=False,
                headers=headers,
            ) as client:
                r = client.get(url)
        except Exception:
            log.info("Flipkart share-link lookup failed")
            return last_loc
        loc = r.headers.get("location")
        if loc:
            return urljoin(str(r.url), loc)
        if r.status_code not in {403, 429, 503}:
            return None
        time.sleep(0.35 * (attempt + 1))
    return last_loc


def _money(obj: object) -> tuple[float | None, str | None]:
    if not isinstance(obj, dict) or obj.get("amount") is None:
        return None, None
    try:
        amount = float(obj["amount"])
    except (TypeError, ValueError):
        return None, None
    currency = obj.get("currency")
    if currency in {"₹", "Rs", "Rs.", "INR"}:
        currency = "INR"
    elif isinstance(currency, str) and len(currency) == 3:
        currency = currency.upper()
    else:
        currency = None
    return amount, currency


def _affiliate_image(imgs: object) -> str | None:
    if not isinstance(imgs, dict) or not imgs:
        return None
    for key in ("400x400", "200x200", "800x800", "unknown"):
        val = imgs.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    for val in imgs.values():
        if isinstance(val, str) and val.startswith("http"):
            return val
    return None


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_affiliate_product(data: dict, pid: str, url: str) -> Product | None:
    """Map Flipkart Affiliate Product API JSON. Does not invent review text."""
    info = data.get("productBaseInfoV1") or data.get("productBaseInfo") or {}
    if not isinstance(info, dict) or not info:
        return None
    price, currency = _money(info.get("flipkartSpecialPrice"))
    if price is None:
        price, currency = _money(info.get("flipkartSellingPrice"))
    if price is None:
        price, currency = _money(info.get("maximumRetailPrice"))
    brand = info.get("productBrand") or info.get("brand")
    rating = _opt_float(
        info.get("averageRating")
        or info.get("productRating")
        or info.get("rating")
    )
    rating_count = _opt_int(info.get("ratingCount") or info.get("numRatings") or info.get("totalRatings"))
    review_count = _opt_int(info.get("reviewCount") or info.get("numReviews") or info.get("totalReviews"))
    product_url = info.get("productUrl")
    if isinstance(product_url, str) and product_url.startswith("http"):
        resolved_url = product_url.replace("http://dl.flipkart.com", "https://www.flipkart.com")
        resolved_url = resolved_url.replace("https://dl.flipkart.com", "https://www.flipkart.com")
    else:
        resolved_url = url
    return Product(
        platform="flipkart",
        product_id=str(info.get("productId") or pid),
        url=resolved_url,
        title=info.get("title") if isinstance(info.get("title"), str) else None,
        brand=brand if isinstance(brand, str) else None,
        category=info.get("categoryPath") if isinstance(info.get("categoryPath"), str) else None,
        price=price,
        currency=currency,
        rating=rating,
        rating_count=rating_count,
        review_count=review_count,
        image_url=_affiliate_image(info.get("imageUrls")),
        extra={"marketplace": "flipkart.com", "pid": pid, "catalog_source": "flipkart_affiliate"},
    )


class FlipkartProvider(BaseProvider):
    name = "flipkart"
    platform = "flipkart"
    display_name = "Flipkart"

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}

    def supports_url(self, url: str) -> bool:
        return detect_platform(url).platform == "flipkart"

    def is_configured(self) -> bool:
        return True

    def health_check(self) -> dict:
        backends = []
        if SERPAPI_API_KEY:
            backends.append("serpapi_google")
        if FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN:
            backends.append("flipkart_affiliate")
        req = for_platform("flipkart")
        return {
            "provider": self.name,
            "configured": self.is_configured(),
            "backends": backends,
            "reviews": (
                "No official review API. Optional SerpAPI Google snippets only if they match this pid."
                if SERPAPI_API_KEY
                else "not configured"
            ),
            **req,
        }

    def configuration_help(self) -> str:
        return (
            "Catalog: set FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN "
            "(https://affiliate.flipkart.com/). That API does not return review text. "
            "No Flipkart review-capable API exists to configure."
        )

    def fetch_product(self, url: str) -> Product | None:
        if not self.is_configured():
            return None
        url = resolve_flipkart_product_url(url)
        pid, item_id, title = extract_flipkart_ids(url)
        affiliate = self._affiliate_product(url, pid) if pid else None
        google = self._google_payload(url, pid, title) if SERPAPI_API_KEY and looks_like_flipkart_pid(pid) else None
        match = self._product_listing_row(google, pid, item_id, url) if google else None
        if match is None and google:
            match = self._catalog_stats_row(google, pid)
        price, currency, rating, review_count = _snippet_stats(match)
        product_id = None
        if affiliate and affiliate.product_id:
            product_id = affiliate.product_id
        elif pid:
            product_id = pid
        resolved_title = _clean_title(
            (affiliate.title if affiliate else None) or (match.get("title") if match else None),
            title,
        )
        if not resolved_title and not product_id:
            return None
        brand = affiliate.brand if affiliate else None
        category = affiliate.category if affiliate else None
        if affiliate and affiliate.price is not None:
            price = affiliate.price
            currency = affiliate.currency or currency
        if affiliate and affiliate.rating is not None:
            rating = affiliate.rating
        if affiliate and affiliate.rating_count is not None:
            review_count = affiliate.rating_count
        if affiliate and affiliate.review_count is not None:
            review_count = affiliate.review_count
        extra = {"marketplace": "flipkart.com"}
        if item_id:
            extra["item_id"] = item_id
        if pid:
            extra["pid"] = pid
        extra["catalog_source"] = "flipkart_affiliate" if affiliate else ("serpapi_google" if match else "url_pid_only")
        return Product(
            platform="flipkart",
            product_id=product_id,
            url=(affiliate.url if affiliate else None) or (match.get("link") if match else None) or url,
            title=resolved_title,
            brand=brand,
            category=category,
            price=price,
            currency=currency,
            rating=rating,
            rating_count=affiliate.rating_count if affiliate and affiliate.rating_count is not None else review_count,
            review_count=review_count,
            image_url=affiliate.image_url if affiliate else None,
            extra=extra,
        )

    def fetch_reviews(self, url: str, limit: int | None = None) -> list[Review]:
        if not SERPAPI_API_KEY:
            return []
        cap = limit or MAX_REVIEWS
        url = resolve_flipkart_product_url(url)
        pid, item_id, title = extract_flipkart_ids(url)
        if not looks_like_flipkart_pid(pid):
            return []
        slug = _product_slug(url)
        data = self._google_payload(url, pid, title)
        rows = self._matching_organics(data, pid, item_id, slug)
        review_data = self._google_search(f'{pid} site:flipkart.com "Certified Buyer"')
        rows.extend(self._matching_organics(review_data, pid, item_id, slug))
        review_pages = self._google_search(f"site:flipkart.com/product-reviews {pid}")
        rows.extend(self._matching_organics(review_pages, pid, item_id, slug))
        if slug:
            slug_reviews = self._google_search(f"site:flipkart.com {slug} product-reviews")
            rows.extend(self._matching_organics(slug_reviews, pid, item_id, slug))
        out: list[Review] = []
        seen: set[str] = set()
        for i, row in enumerate(rows):
            if len(out) >= cap:
                break
            if not _is_review_row(row, pid, item_id, slug):
                continue
            text = _review_text_from_organic(row)
            if not text or text in seen:
                continue
            title_text = row.get("title") if isinstance(row.get("title"), str) else None
            if title_text and (title_text.startswith("http") or _is_junk_link(title_text)):
                title_text = None
            seen.add(text)
            rating = _organic_rating(row)
            out.append(
                Review(
                    platform="flipkart",
                    product_id=pid,
                    review_id=f"{pid}-{i+1}",
                    reviewer_name=None,
                    reviewer_id=None,
                    rating=rating,
                    title=title_text,
                    text=text,
                )
            )
        return out

    def _affiliate_product(self, url: str, pid: str | None) -> Product | None:
        if not (FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN and pid):
            return None
        cache_key = f"aff:{pid.upper()}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return parse_affiliate_product(cached, pid, url) if cached else None
        headers = {
            "Fk-Affiliate-Id": FLIPKART_AFFILIATE_ID,
            "Fk-Affiliate-Token": FLIPKART_AFFILIATE_TOKEN,
        }
        data = None
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SEC) as client:
                for endpoint in _AFFILIATE_ENDPOINTS:
                    r = client.get(endpoint, params={"id": pid}, headers=headers)
                    if r.status_code >= 400:
                        log.warning("Flipkart affiliate HTTP %s for %s", r.status_code, endpoint)
                        continue
                    payload = r.json()
                    if isinstance(payload, dict) and (
                        payload.get("productBaseInfoV1") or payload.get("productBaseInfo")
                    ):
                        data = payload
                        break
        except Exception:
            log.warning("Flipkart affiliate request failed")
            return None
        if len(self._cache) >= _CACHE_MAX:
            self._cache.clear()
        self._cache[cache_key] = data or {}
        return parse_affiliate_product(data, pid, url) if data else None

    def _google_payload(self, url: str, pid: str | None, title: str | None) -> dict | None:
        key = f"g:{(pid or url).upper()}"
        if key in self._cache:
            return self._cache[key]
        _, item_id, _ = extract_flipkart_ids(url)
        slug = _product_slug(url)
        queries: list[str] = []
        if pid:
            queries.append(f"site:flipkart.com {pid}")
        if slug:
            queries.append(f"site:flipkart.com {slug}")
            queries.append(f"site:flipkart.com {slug} product-reviews")
        if item_id:
            queries.append(f"site:flipkart.com {item_id}")
        merged: list[dict] = []
        seen: set[str] = set()
        for query in queries:
            data = self._google_search(query)
            for row in (data or {}).get("organic_results") or []:
                if not isinstance(row, dict):
                    continue
                link = str(row.get("link") or "")
                if not link or link in seen:
                    continue
                seen.add(link)
                merged.append(row)
        payload = {"organic_results": merged} if merged else None
        if payload is not None:
            if len(self._cache) >= _CACHE_MAX:
                self._cache.clear()
            self._cache[key] = payload
        return payload

    def _google_search(self, query: str) -> dict | None:
        if not SERPAPI_API_KEY or not query:
            return None
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SEC) as client:
                r = client.get(
                    "https://serpapi.com/search.json",
                    params={
                        "engine": "google",
                        "q": query,
                        "gl": "in",
                        "hl": "en",
                        "api_key": SERPAPI_API_KEY,
                    },
                )
                if r.status_code >= 400:
                    log.warning("Authorized API HTTP %s from SerpAPI Google", r.status_code)
                    return None
                data = r.json()
                return data if isinstance(data, dict) and not data.get("error") else None
        except Exception:
            log.warning("Flipkart SerpAPI request failed")
            return None

    def _product_listing_row(
        self, data: dict | None, pid: str | None, item_id: str | None, url: str | None = None
    ) -> dict | None:
        slug = _product_slug(url or "")
        for row in self._matching_organics(data, pid, item_id, slug):
            link = _row_link(row).lower()
            if _is_product_page(link, pid, item_id, slug) and "/product-reviews/" not in link:
                return row
        return None

    def _catalog_stats_row(self, data: dict | None, pid: str | None) -> dict | None:
        if not data or not pid:
            return None
        for row in data.get("organic_results") or []:
            if not isinstance(row, dict):
                continue
            if pid.lower() not in _row_link(row).lower():
                continue
            price, _, rating, reviews = _snippet_stats(row)
            if price is not None or rating is not None or reviews is not None:
                return row
        return None

    def _matching_organics(
        self, data: dict | None, pid: str | None, item_id: str | None, slug: str | None = None
    ) -> list[dict]:
        if not data:
            return []
        out = []
        for row in data.get("organic_results") or []:
            if not isinstance(row, dict):
                continue
            if _row_matches_product(row, pid, item_id, slug):
                out.append(row)
        return out


def _row_link(row: dict) -> str:
    return f"{row.get('link') or ''} {row.get('displayed_link') or ''} {row.get('redirect_link') or ''}"


def _is_junk_link(link: str) -> bool:
    low = (link or "").lower()
    return any(part in low for part in _NON_PRODUCT_PATHS)


def _is_product_page(link: str, pid: str | None, item_id: str | None, slug: str | None = None) -> bool:
    low = (link or "").lower()
    if "flipkart.com" not in low or _is_junk_link(low):
        return False
    if "/p/" not in low and "/product-reviews/" not in low:
        return False
    if pid and pid.lower() in low:
        return True
    if slug and slug in low and item_id and item_id.lower() in low:
        return True
    return False


def _row_matches_product(row: dict, pid: str | None, item_id: str | None, slug: str | None = None) -> bool:
    link = _row_link(row).lower()
    if "flipkart.com" not in link:
        return False
    if pid and pid.lower() in link:
        return True
    if slug and slug in link and item_id and item_id.lower() in link:
        return True
    return False


def _clean_title(candidate: str | None, fallback: str | None) -> str | None:
    text = (candidate or "").strip()
    if not text or text.startswith("http") or _is_junk_link(text):
        return fallback
    return text


def _is_review_row(row: dict, pid: str | None, item_id: str | None, slug: str | None = None) -> bool:
    link = _row_link(row).lower()
    if not _row_matches_product(row, pid, item_id, slug):
        return False
    if _is_junk_link(link):
        return False
    snippet = (row.get("snippet") or "").lower()
    if any(marker in snippet for marker in _JUNK_SNIPPETS):
        return False
    if "/product-reviews/" in link or "certified buyer" in snippet:
        return True
    return _is_product_page(link, pid, item_id, slug)


def _snippet_stats(row: dict | None) -> tuple[float | None, str | None, float | None, int | None]:
    if not row:
        return None, None, None, None
    ext = ((row.get("rich_snippet") or {}).get("top") or {}).get("detected_extensions") or {}
    if not isinstance(ext, dict):
        ext = {}
    price = ext.get("price_from") if isinstance(ext.get("price_from"), (int, float)) else ext.get("price")
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    currency = None
    if ext.get("currency") in {"₹", "INR", "Rs", "Rs."}:
        currency = "INR"
    elif isinstance(ext.get("currency"), str) and len(ext["currency"]) == 3:
        currency = ext["currency"].upper()
    rating = ext.get("rating")
    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None
    reviews = ext.get("reviews")
    try:
        reviews_i = int(reviews) if reviews is not None else None
    except (TypeError, ValueError):
        reviews_i = None
    return price_f, currency, rating_f, reviews_i


def _organic_rating(row: dict) -> float | None:
    _, _, rating, _ = _snippet_stats(row)
    return rating


def _review_text_from_organic(row: dict) -> str | None:
    snippet = (row.get("snippet") or "").strip()
    if len(snippet) < 40:
        return None
    low = snippet.lower()
    if any(m in low for m in _DEAL_MARKERS) or any(m in low for m in _JUNK_SNIPPETS):
        return None
    if snippet.startswith("http"):
        return None
    return snippet
