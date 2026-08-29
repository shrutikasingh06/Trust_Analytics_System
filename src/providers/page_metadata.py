"""Public product-page metadata: one GET, JSON-LD / Open Graph / title / URL slug.

This is not a storefront scrape of review widgets, not a CAPTCHA bypass, and not login.
If the host returns 403/empty HTML, fields stay missing instead of being invented.
JSON-LD Review.reviewBody is used only when the page itself publishes it.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import httpx

from src.config import HTTP_TIMEOUT_SEC, MAX_REVIEWS, SERPAPI_API_KEY
from src.product_identity import extract_store_product_id
from src.providers.serp_catalog import google_catalog, google_reviews, _split_store_title
from src.schemas import Product, Review

log = logging.getLogger("trust_analytics.providers.page_metadata")
logging.getLogger("httpx").setLevel(logging.WARNING)

LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META_RE = re.compile(
    r"<meta\b([^>]+)>",
    re.I,
)
ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*["']([^"']*)["']""", re.I)
_GENERIC_SEGMENTS = {
    "p",
    "product",
    "products",
    "item",
    "itme",
    "dp",
    "gp",
    "buy",
    "www",
    "en-in",
    "in",
    "search",
    "catalog",
}
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.google.com/",
}
_ITEM_PRICE = re.compile(
    r'itemprop=["\']price["\'][^>]*content=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'][^>]*itemprop=["\']price["\']',
    re.I,
)
_ITEM_CUR = re.compile(
    r'itemprop=["\']priceCurrency["\'][^>]*content=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'][^>]*itemprop=["\']priceCurrency["\']',
    re.I,
)
_CACHE_MAX = 32
_html_cache: dict[str, str | None] = {}
_catalog_cache: dict[tuple[str, str, bool], tuple[Product | None, list[Review]]] = {}


def clear_catalog_caches() -> None:
    _html_cache.clear()
    _catalog_cache.clear()


def fetch_public_html(url: str) -> str | None:
    key = (url or "").strip()
    if not key:
        return None
    if key in _html_cache:
        return _html_cache[key]
    html = None
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SEC, follow_redirects=True, headers=_BROWSER_HEADERS) as client:
            r = client.get(key)
            body = r.text or ""
            if body and (r.status_code < 400 or "og:title" in body.lower() or "application/ld+json" in body.lower()):
                html = body
    except Exception:
        log.info("Public product page GET failed")
        html = None
    if len(_html_cache) >= _CACHE_MAX:
        _html_cache.clear()
    _html_cache[key] = html
    return html


def _review_urls(platform: str, url: str) -> list[str]:
    pid = extract_store_product_id(platform, url)
    parsed = urlparse(url if "://" in (url or "") else f"https://{url}")
    parts = [p for p in (parsed.path or "").split("/") if p]
    if platform == "nykaa" and pid:
        slug = None
        if "p" in parts:
            idx = parts.index("p")
            if idx >= 1:
                slug = parts[idx - 1]
        elif "reviews" in parts:
            idx = parts.index("reviews")
            if idx >= 1:
                slug = parts[idx - 1]
        if slug:
            return [f"https://www.nykaa.com/{slug}/reviews/{pid}"]
    if platform == "myntra" and pid:
        return [f"https://www.myntra.com/reviews/{pid}"]
    return []


def catalog_from_url(url: str, platform: str, *, use_google: bool = False) -> tuple[Product | None, list[Review]]:
    """Product fields from public HTML/Google catalog. Reviews from JSON-LD or matching Google review snippets."""
    cache_key = ((url or "").strip(), platform, bool(use_google))
    if cache_key in _catalog_cache:
        return _catalog_cache[cache_key]
    html = fetch_public_html(url)
    page_product, reviews = parse_public_page(html, url, platform) if html else (None, [])
    if not reviews:
        for review_url in _review_urls(platform, url):
            if review_url.rstrip("/") == (url or "").rstrip("/"):
                continue
            review_html = fetch_public_html(review_url)
            if not review_html:
                continue
            _, more = parse_public_page(review_html, review_url, platform)
            reviews.extend(more)
            if reviews:
                break
    store_id = extract_store_product_id(platform, url)
    if store_id and page_product and page_product.product_id:
        sku = str(page_product.product_id).lower()
        sid = store_id.lower()
        if sid not in sku and sku not in sid and sku.replace("_", "") not in sid.replace("_", ""):
            page_product = None
            reviews = []
    slug_product = product_from_url_slug(url, platform)
    product = overlay_product(page_product, slug_product)
    if store_id and product and not product.product_id:
        product = product.model_copy(update={"product_id": store_id})
    elif store_id and product is None:
        product = Product(platform=platform, url=url, product_id=store_id, extra={"catalog_source": "url_id"})
    needs_google = use_google and SERPAPI_API_KEY and (
        product is None
        or not product.title
        or product.price is None
        or product.rating is None
        or not product.brand
        or not product.image_url
    )
    g_reviews: list[Review] = []
    if use_google and SERPAPI_API_KEY:
        if needs_google:
            g_product, g_reviews = google_catalog(url, platform)
            product = overlay_product(product, g_product)
        if not reviews:
            extra = google_reviews(url, platform)
            reviews = extra or g_reviews
    if len(_catalog_cache) >= _CACHE_MAX:
        _catalog_cache.clear()
    _catalog_cache[cache_key] = (product, reviews)
    return product, reviews


def parse_public_page(html: str, url: str, platform: str) -> tuple[Product | None, list[Review]]:
    metas = _meta_map(html)
    ld_product = None
    reviews: list[Review] = []
    for block in LD_RE.findall(html):
        text = re.sub(r"<!--.*?-->", "", block, flags=re.S).strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        prod, more = _from_jsonld(data, url, platform)
        if prod and ld_product is None:
            ld_product = prod
        reviews.extend(more)
        if len(reviews) >= MAX_REVIEWS:
            reviews = reviews[:MAX_REVIEWS]
            break
    og_product = _from_meta(metas, url, platform)
    title_tag = None
    tm = TITLE_RE.search(html)
    if tm:
        title_tag = _clean_title(re.sub(r"<[^>]+>", "", tm.group(1)))
    titled = None
    if title_tag:
        titled = Product(platform=platform, url=url, title=title_tag, extra={"catalog_source": "html_title"})
    product = overlay_product(overlay_product(ld_product, og_product), titled)
    micro = _from_microdata(html, url, platform)
    product = overlay_product(product, micro)
    return product, reviews


def product_from_url_slug(url: str, platform: str) -> Product | None:
    parsed = urlparse(url if "://" in (url or "") else f"https://{url}")
    parts = [p for p in (parsed.path or "").split("/") if p]
    title = None
    for idx, part in enumerate(reversed(parts)):
        low = part.lower()
        if idx == 0 and len(parts) >= 2 and parts[-2].lower() in {"p", "buy"}:
            continue
        if low in _GENERIC_SEGMENTS or low.startswith("itm") or re.fullmatch(r"[a-z0-9]{10}", low):
            continue
        if re.fullmatch(r"\d+", part):
            continue
        if len(part) < 4:
            continue
        title = part.replace("-", " ").replace("_", " ").strip().title() or None
        if title:
            break
    category = None
    if parts:
        head = parts[0].replace("-", " ").strip()
        if head.lower() not in _GENERIC_SEGMENTS and not re.fullmatch(r"\d+", parts[0]) and len(head) >= 3:
            category = head.title()
    extra = {"catalog_source": "url_slug", "marketplace": parsed.netloc.lower().lstrip("www.")}
    if not title:
        return Product(platform=platform, url=url, category=category, extra=extra) if url else None
    return Product(platform=platform, url=url, title=title, category=category, extra=extra)


def overlay_product(base: Product | None, extra: Product | None) -> Product | None:
    if extra is None:
        return base
    if base is None:
        return extra
    updates: dict = {}
    for field in (
        "product_id",
        "url",
        "title",
        "brand",
        "category",
        "price",
        "currency",
        "rating",
        "rating_count",
        "review_count",
        "image_url",
    ):
        cur = getattr(base, field)
        alt = getattr(extra, field)
        if cur in (None, "") and alt not in (None, ""):
            updates[field] = alt
        elif (
            field == "title"
            and alt not in (None, "")
            and (
                (base.extra or {}).get("catalog_source") == "url_slug"
                or _is_generic_title(str(cur or ""))
            )
        ):
            updates[field] = alt
    if not (base.brand or updates.get("brand")):
        _, guessed = _split_store_title(extra.title or base.title)
        if guessed:
            updates["brand"] = guessed
    merged_extra = {**(base.extra or {}), **(extra.extra or {})}
    if merged_extra:
        updates["extra"] = merged_extra
    return base.model_copy(update=updates) if updates else base


def _meta_map(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in META_RE.findall(html):
        attrs = {k.lower(): v for k, v in ATTR_RE.findall(raw)}
        key = attrs.get("property") or attrs.get("name") or ""
        content = attrs.get("content")
        if key and content:
            out[key.lower()] = content.strip()
    return out


def _from_meta(metas: dict[str, str], url: str, platform: str) -> Product | None:
    title = _clean_title(metas.get("og:title") or metas.get("twitter:title") or metas.get("title"))
    brand = metas.get("product:brand") or metas.get("og:brand")
    image = metas.get("og:image") or metas.get("twitter:image")
    price = _safe_float(metas.get("product:price:amount") or metas.get("og:price:amount"))
    currency = metas.get("product:price:currency") or metas.get("og:price:currency")
    if currency:
        currency = currency.upper()
        if currency in {"₹", "RS", "RS."}:
            currency = "INR"
    if not any([title, brand, price, image]):
        return None
    return Product(
        platform=platform,
        url=metas.get("og:url") or url,
        title=title,
        brand=brand,
        price=price,
        currency=currency,
        image_url=image if image and image.startswith("http") else None,
        extra={"catalog_source": "open_graph"},
    )


def _from_jsonld(data, url: str, platform: str) -> tuple[Product | None, list[Review]]:
    product = None
    reviews: list[Review] = []
    for node in _nodes(data):
        types = _types(node)
        if "Product" in types and product is None:
            product = _product_node(node, url, platform)
        if "Review" in types:
            rev = _review_node(node, platform)
            if rev:
                reviews.append(rev)
        if isinstance(node.get("review"), list):
            for item in node["review"]:
                if isinstance(item, dict):
                    rev = _review_node(item, platform)
                    if rev:
                        reviews.append(rev)
        elif isinstance(node.get("review"), dict):
            rev = _review_node(node["review"], platform)
            if rev:
                reviews.append(rev)
    return product, reviews


def _nodes(data) -> list[dict]:
    nodes = data if isinstance(data, list) else [data]
    graph = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if "@graph" in node:
            extra = node["@graph"]
            graph.extend(extra if isinstance(extra, list) else [extra])
        graph.append(node)
    return [n for n in graph if isinstance(n, dict)]


def _types(node: dict) -> list[str]:
    types = node.get("@type")
    raw = types if isinstance(types, list) else ([types] if types else [])
    out = []
    for t in raw:
        s = str(t)
        out.append(s.rsplit("/", 1)[-1] if "://" in s else s)
    return out


def _from_microdata(html: str, url: str, platform: str) -> Product | None:
    pm = _ITEM_PRICE.search(html or "")
    cm = _ITEM_CUR.search(html or "")
    price = _safe_float((pm.group(1) or pm.group(2)) if pm else None)
    currency = (cm.group(1) or cm.group(2)) if cm else None
    if currency:
        currency = currency.upper()
        if currency in {"₹", "RS", "RS."}:
            currency = "INR"
    if price is None and not currency:
        return None
    return Product(
        platform=platform,
        url=url,
        price=price,
        currency=currency,
        extra={"catalog_source": "microdata"},
    )


def _product_node(node: dict, url: str, platform: str) -> Product | None:
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
    title = node.get("name") if isinstance(node.get("name"), str) else None
    return Product(
        platform=platform,
        product_id=_as_str(node.get("sku") or node.get("productID") or node.get("mpn")),
        url=_as_str(node.get("url")) or url,
        title=_clean_title(title),
        brand=brand if isinstance(brand, str) else None,
        category=node.get("category") if isinstance(node.get("category"), str) else None,
        price=_safe_float(offers.get("price") if isinstance(offers, dict) else None),
        currency=(
            str(offers.get("priceCurrency")).upper()
            if isinstance(offers, dict) and offers.get("priceCurrency")
            else None
        ),
        rating=_safe_float(agg.get("ratingValue") if isinstance(agg, dict) else None),
        rating_count=_safe_int(agg.get("ratingCount") if isinstance(agg, dict) else None),
        review_count=_safe_int(agg.get("reviewCount") if isinstance(agg, dict) else None),
        image_url=image if isinstance(image, str) else None,
        extra={"catalog_source": "jsonld"},
    )


def _review_node(node: dict, platform: str) -> Review | None:
    if "Review" not in _types(node) and not node.get("reviewBody"):
        return None
    text = node.get("reviewBody") or node.get("description")
    if not isinstance(text, str) or len(text.strip()) < 20:
        return None
    rating_obj = node.get("reviewRating") or {}
    rating = _safe_float(rating_obj.get("ratingValue") if isinstance(rating_obj, dict) else None)
    author = node.get("author")
    name = None
    if isinstance(author, dict):
        name = author.get("name") if isinstance(author.get("name"), str) else None
    elif isinstance(author, str):
        name = author
    return Review(
        platform=platform,
        review_id=_as_str(node.get("@id") or node.get("sku")),
        reviewer_name=name,
        rating=rating,
        title=node.get("name") if isinstance(node.get("name"), str) else None,
        text=text.strip(),
    )


def _clean_title(text: str | None) -> str | None:
    if not text:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    value = re.sub(r"^(?:Buy|Shop)\s+", "", value, flags=re.I)
    value = re.split(r"\s+\|\s+| Price in India| - Buy | Online$", value, maxsplit=1)[0].strip()
    if not value or _is_generic_title(value):
        return None
    return value[:300]


def _is_generic_title(text: str | None) -> bool:
    low = re.sub(r"\s+", " ", (text or "")).strip().lower()
    if not low:
        return True
    if low in {
        "online shopping site",
        "flipkart.com",
        "online electronics shopping",
        "reliance digital",
        "croma",
        "ajio",
        "myntra",
        "nykaa",
        "meesho",
        "tata cliq",
        "access denied",
        "page not found",
    }:
        return True
    if "online electronic shopping" in low or "online electronics shopping" in low:
        return True
    if low.endswith(" reviews online"):
        return True
    return False


def _as_str(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _safe_float(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None
