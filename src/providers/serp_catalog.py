"""SerpAPI Google catalog lookup for storefronts that block a direct HTML GET.

Uses the existing SERPAPI_API_KEY. Results must match this store host and product id.
Does not invent fields or map other stores onto Amazon.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from src.config import HTTP_TIMEOUT_SEC, MAX_REVIEWS, SERPAPI_API_KEY
from src.product_identity import extract_store_product_id
from src.schemas import Product, Review

log = logging.getLogger("trust_analytics.providers.serp_catalog")
logging.getLogger("httpx").setLevel(logging.WARNING)

_HOSTS: dict[str, tuple[str, ...]] = {
    "myntra": ("myntra.com",),
    "nykaa": ("nykaa.com", "nykaafashion.com"),
    "meesho": ("meesho.com",),
    "ajio": ("ajio.com",),
    "croma": ("croma.com",),
    "reliance_digital": ("reliancedigital.in",),
    "tata_cliq": ("tatacliq.com",),
    "flipkart": ("flipkart.com",),
}
_SOURCE_NAMES: dict[str, tuple[str, ...]] = {
    "myntra": ("myntra",),
    "nykaa": ("nykaa",),
    "meesho": ("meesho",),
    "ajio": ("ajio",),
    "croma": ("croma",),
    "reliance_digital": ("reliance digital", "reliancedigital"),
    "tata_cliq": ("tata cliq", "tatacliq"),
    "flipkart": ("flipkart",),
}
_INR_PRICE = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_STAR = re.compile(r"\b(\d(?:\.\d)?)\s*(?:out of 5|stars?|★)", re.I)
_DEAL = ("% off", "hot deal", "free delivery", "in stock", "flash sale", "shop now", "buy now")
_REVIEWISH = (
    "certified buyer",
    "i bought",
    "i purchased",
    "i used",
    "my order",
    "quality is",
    "fabric",
    "fitting",
    "delivery",
    "worth the",
    "not as shown",
    "as described",
    "customer review",
)
_CACHE: dict[str, dict | None] = {}
_CACHE_MAX = 40


def google_catalog(url: str, platform: str) -> tuple[Product | None, list[Review]]:
    if not SERPAPI_API_KEY or platform not in _HOSTS:
        return None, []
    hosts = _HOSTS[platform]
    names = _SOURCE_NAMES.get(platform, ())
    pid = extract_store_product_id(platform, url)
    parsed = urlparse(url if "://" in (url or "") else f"https://{url}")
    page_host = (parsed.netloc or "").lower().lstrip("www.")
    site = page_host if any(h in page_host for h in hosts) else hosts[0]
    slug = [p for p in (parsed.path or "").split("/") if p and p.lower() not in {"p", "buy", "dp", "product"}]
    queries: list[str] = []
    if pid:
        queries.append(f"site:{site} {pid}")
    if slug:
        queries.append(f"site:{site} {' '.join(slug[-3:])}")
    queries.append(url)

    rows: list[dict] = []
    shopping: list[dict] = []
    knowledge: dict | None = None
    for query in queries:
        data = _search(query)
        kg = data.get("knowledge_graph") if isinstance(data.get("knowledge_graph"), dict) else None
        if kg and knowledge is None:
            knowledge = kg
        cand = [
            r
            for r in (data.get("organic_results") or [])
            if isinstance(r, dict) and _matches(r, hosts, names, pid, url)
        ]
        shop = [
            r
            for r in (data.get("shopping_results") or data.get("inline_shopping") or [])
            if isinstance(r, dict) and _matches(r, hosts, names, pid, url)
        ]
        cand.sort(key=_rank, reverse=True)
        if cand or shop:
            rows, shopping = cand, shop
            break

    product = _from_rows(platform, pid, url, site, rows, shopping)
    if knowledge:
        product = _overlay_knowledge(product, knowledge, platform, pid, url)
    if product is not None and product.price is None and pid:
        shop_data = _search(f"site:{site} {pid}", shop=True)
        extra_shop = [
            r
            for r in (shop_data.get("shopping_results") or shop_data.get("inline_shopping") or [])
            if isinstance(r, dict) and _matches(r, hosts, names, pid, url)
        ]
        if extra_shop:
            priced = _from_rows(platform, pid, url, site, rows, extra_shop)
            if priced and priced.price is not None:
                product = product.model_copy(
                    update={
                        "price": priced.price,
                        "currency": priced.currency or product.currency,
                    }
                )
    reviews = _reviews_from_rows(platform, pid, rows) if product else []
    extra = google_reviews(url, platform)
    if extra:
        reviews = extra
    return product, reviews


def google_reviews(url: str, platform: str) -> list[Review]:
    """Google organic snippets from this store's review pages for this product id only."""
    if not SERPAPI_API_KEY or platform not in _HOSTS:
        return []
    hosts = _HOSTS[platform]
    names = _SOURCE_NAMES.get(platform, ())
    pid = extract_store_product_id(platform, url)
    parsed = urlparse(url if "://" in (url or "") else f"https://{url}")
    page_host = (parsed.netloc or "").lower().lstrip("www.")
    site = page_host if any(h in page_host for h in hosts) else hosts[0]
    slug = [p for p in (parsed.path or "").split("/") if p and p.lower() not in {"p", "buy", "dp", "product", "reviews"}]
    queries: list[str] = []
    if pid:
        queries.append(f"site:{site} {pid} reviews")
        queries.append(f"site:{site}/reviews {pid}")
    if slug:
        queries.append(f"site:{site} {' '.join(slug[-3:])} reviews")
    rows: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        data = _search(query)
        for row in data.get("organic_results") or []:
            if not isinstance(row, dict) or not _matches(row, hosts, names, pid, url):
                continue
            if not _looks_like_review_result(row, pid):
                continue
            link = str(row.get("link") or "")
            if link in seen:
                continue
            seen.add(link)
            rows.append(row)
    return _reviews_from_rows(platform, pid, rows)


def _looks_like_review_result(row: dict, pid: str | None) -> bool:
    link = str(row.get("link") or "").lower()
    if "/reviews/" not in link and "/product-reviews/" not in link and "review" not in link:
        return False
    if pid and pid.lower() not in f"{link} {row.get('snippet') or ''}".lower():
        return False
    return True


def _from_rows(
    platform: str,
    pid: str | None,
    url: str,
    site: str,
    rows: list[dict],
    shopping: list[dict],
) -> Product | None:
    if not rows and not shopping:
        return None
    best = rows[0] if rows else shopping[0]
    price, currency, rating, review_count = _stats(best)
    if price is None or rating is None:
        for extra in shopping + rows[1:]:
            p2, c2, r2, n2 = _stats(extra)
            if price is None and p2 is not None:
                price, currency = p2, c2 or currency
            if rating is None:
                rating = r2
            if review_count is None:
                review_count = n2
            if price is not None and rating is not None:
                break
    title = best.get("title") if isinstance(best.get("title"), str) else None
    if title and title.startswith("http"):
        title = None
    title, brand = _split_store_title(title)
    image = best.get("thumbnail") if isinstance(best.get("thumbnail"), str) else None
    if image and not str(image).startswith("http"):
        image = None
    return Product(
        platform=platform,
        product_id=pid,
        url=best.get("link") if isinstance(best.get("link"), str) else url,
        title=title,
        brand=brand,
        price=price,
        currency=currency or ("INR" if price is not None else None),
        rating=rating,
        rating_count=review_count,
        review_count=review_count,
        image_url=image,
        extra={"catalog_source": "serpapi_google", "marketplace": site},
    )


def _overlay_knowledge(
    product: Product | None, kg: dict, platform: str, pid: str | None, url: str
) -> Product | None:
    title = kg.get("title") if isinstance(kg.get("title"), str) else None
    title, brand = _split_store_title(title)
    rating = kg.get("rating")
    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None
    reviews = kg.get("review_count") or kg.get("reviews")
    try:
        reviews_i = int(str(reviews).replace(",", "")) if reviews is not None else None
    except (TypeError, ValueError):
        reviews_i = None
    extra = Product(
        platform=platform,
        product_id=pid,
        url=url,
        title=title,
        brand=brand,
        rating=rating_f,
        rating_count=reviews_i,
        review_count=reviews_i,
        extra={"catalog_source": "serpapi_google_kg"},
    )
    if product is None:
        return extra
    updates = {}
    for field in ("title", "brand", "rating", "rating_count", "review_count"):
        if getattr(product, field) in (None, "") and getattr(extra, field) not in (None, ""):
            updates[field] = getattr(extra, field)
    return product.model_copy(update=updates) if updates else product


def _reviews_from_rows(platform: str, pid: str | None, rows: list[dict]) -> list[Review]:
    out: list[Review] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if len(out) >= MAX_REVIEWS:
            break
        text = _review_text(row)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(
            Review(
                platform=platform,
                product_id=pid,
                review_id=f"{pid or platform}-g{i+1}",
                rating=_stats(row)[2],
                title=row.get("title") if isinstance(row.get("title"), str) else None,
                text=text,
            )
        )
    return out


def _review_text(row: dict) -> str | None:
    snippet = (row.get("snippet") or "").strip()
    if len(snippet) < 40:
        return None
    low = snippet.lower()
    if any(m in low for m in _DEAL) or "₹" in snippet:
        return None
    if snippet.startswith("http"):
        return None
    link = str(row.get("link") or "").lower()
    on_review_page = "/reviews/" in link or "/product-reviews/" in link
    if on_review_page:
        if "rating" in low and "verified rating" in low:
            return None
        return snippet
    if not any(m in low for m in _REVIEWISH):
        return None
    return snippet


def _search(query: str, shop: bool = False) -> dict:
    key = ("shop:" if shop else "") + query.strip().lower()
    if key in _CACHE:
        return _CACHE[key] or {}
    payload = None
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SEC) as client:
            params = {"engine": "google", "q": query, "gl": "in", "hl": "en", "api_key": SERPAPI_API_KEY}
            if shop:
                params["tbm"] = "shop"
            r = client.get("https://serpapi.com/search.json", params=params)
            if r.status_code < 400:
                data = r.json()
                payload = data if isinstance(data, dict) and not data.get("error") else None
    except Exception:
        log.info("SerpAPI Google catalog lookup failed")
        payload = None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = payload
    return payload or {}


def _matches(row: dict, hosts: tuple[str, ...], names: tuple[str, ...], pid: str | None, url: str) -> bool:
    link = f"{row.get('link') or ''} {row.get('displayed_link') or ''} {row.get('source') or ''} {row.get('title') or ''}".lower()
    host_ok = any(host in link for host in hosts) or any(name in link for name in names)
    if not host_ok:
        return False
    blob = f"{link} {row.get('snippet') or ''}".lower()
    if pid:
        token = pid.lower()
        if token in blob:
            return True
        compact = token.replace("_", "").replace("-", "")
        if len(compact) >= 5 and compact in blob.replace("-", "").replace("_", ""):
            return True
    path = urlparse(url).path.lower()
    parts = [p for p in path.split("/") if len(p) > 4]
    return bool(parts) and any(p in blob for p in parts[-2:])


def _extensions(row: dict) -> dict:
    snippet = row.get("rich_snippet") or {}
    if not isinstance(snippet, dict):
        return {}
    for key in ("top", "bottom"):
        block = snippet.get(key) or {}
        if isinstance(block, dict):
            ext = block.get("detected_extensions") or {}
            if isinstance(ext, dict) and ext:
                return ext
    return {}


def _stats(row: dict) -> tuple[float | None, str | None, float | None, int | None]:
    ext = _extensions(row)
    price = ext.get("price_from") if isinstance(ext.get("price_from"), (int, float)) else ext.get("price")
    if price is None and isinstance(row.get("price"), str):
        m = _INR_PRICE.search(row["price"])
        price = m.group(1) if m else None
    elif price is None and isinstance(row.get("extracted_price"), (int, float)):
        price = row["extracted_price"]
    try:
        if isinstance(price, str):
            price = price.replace(",", "")
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    blob = f"{row.get('snippet') or ''} {row.get('title') or ''}"
    if price_f is None:
        m = _INR_PRICE.search(blob)
        if m:
            try:
                price_f = float(m.group(1).replace(",", ""))
            except ValueError:
                price_f = None
    currency = None
    if ext.get("currency") in {"₹", "INR", "Rs", "Rs."} or (price_f is not None and _INR_PRICE.search(blob)):
        currency = "INR"
    elif isinstance(ext.get("currency"), str) and len(ext["currency"]) == 3:
        currency = ext["currency"].upper()
    rating = ext.get("rating")
    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None
    if rating_f is None:
        sm = _STAR.search(blob)
        if sm:
            try:
                rating_f = float(sm.group(1))
            except ValueError:
                rating_f = None
    reviews = ext.get("reviews")
    try:
        reviews_i = int(reviews) if reviews is not None else None
    except (TypeError, ValueError):
        reviews_i = None
    if reviews_i is not None and reviews_i > 200000:
        reviews_i = None
    return price_f, currency, rating_f, reviews_i


def _rank(row: dict) -> int:
    link = str(row.get("link") or "").lower()
    score = 0
    if any(tok in link for tok in ("/p/", "/buy", "/product/", "/dp/")):
        score += 3
    if "review" in link:
        score -= 1
    return score


def _split_store_title(title: str | None) -> tuple[str | None, str | None]:
    if not title:
        return None, None
    value = re.sub(r"\s+", " ", title).strip()
    value = re.sub(r"^(?:Buy|Shop)\s+", "", value, flags=re.I)
    value = re.split(
        r"\s+\|\s+| Price in India| - Buy |\s+[-–]\s+(?:Myntra|Nykaa|Meesho|Ajio|Croma|Reliance Digital|Tata CLiQ)\b",
        value,
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    brand = None
    if " by " in value.lower():
        left, right = re.split(r"\s+by\s+", value, maxsplit=1, flags=re.I)
        if right and len(right.split()) <= 4:
            brand = right.strip(" .|-")
            value = left.strip()
    return value[:300] or None, brand
