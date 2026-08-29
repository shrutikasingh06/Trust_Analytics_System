"""Amazon product/review access via authorized APIs only (no HTML scrape, no CAPTCHA bypass)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import (
    AMAZON_PAAPI_ACCESS_KEY,
    AMAZON_PAAPI_PARTNER_TAG,
    AMAZON_PAAPI_SECRET_KEY,
    HTTP_TIMEOUT_SEC,
    MAX_REVIEWS,
    RAINFOREST_API_KEY,
    SERPAPI_API_KEY,
)
from src.platform_detector import detect_platform
from src.product_ids import amazon_domain_from_url, extract_asin
from src.providers.base_provider import BaseProvider
from src.schemas import Product, Review

log = logging.getLogger("trust_analytics.providers.amazon")
logging.getLogger("httpx").setLevel(logging.WARNING)

_SERPAPI_CACHE_MAX = 24
_SYMBOL_TO_CURRENCY = {"$": "USD", "₹": "INR", "£": "GBP", "€": "EUR"}
_REVIEW_LIST_KEYS = {
    "authors_reviews",
    "other_countries_reviews",
    "top_reviews",
    "top_positive_reviews",
    "top_critical_reviews",
    "reviews",
}
_TAG_RE = re.compile(r"<[^>]+>")
_STAR_RE = re.compile(r"(\d(?:\.\d)?)\s+out of\s+5", re.I)
_REVIEW_BODY_RE = re.compile(
    r'data-hook="review-body"[^>]*>\s*<span[^>]*>(.*?)</span>',
    re.I | re.S,
)
_REVIEW_TITLE_RE = re.compile(
    r'data-hook="review-title"[^>]*>\s*(?:<span[^>]*>.*?out of 5 stars</span>)?\s*<span[^>]*>(.*?)</span>',
    re.I | re.S,
)
_REVIEW_BLOCK_RE = re.compile(
    r'(?:id="customer_review-[^"]+"|data-hook="review")[\s\S]{0,12000}?(?=id="customer_review-|data-hook="review"|$)',
    re.I,
)


def _serpapi_payload_has_product(data: dict | None) -> bool:
    if not data or data.get("error"):
        return False
    product = data.get("product_results")
    return isinstance(product, dict) and bool(product.get("title") or product.get("asin"))


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            raw = text[:19].replace("Z", "") if "T" in fmt else text
            dt = datetime.strptime(raw, fmt.replace("Z", "") if fmt.endswith("Z") else fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    m = re.search(r"([A-Za-z]+ \d{1,2}, \d{4})", text)
    if m:
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(m.group(1), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _currency_from_display(text: Any) -> str | None:
    """Read a currency code only from provider text/fields. Never infer from marketplace."""
    if isinstance(text, dict):
        for key in ("currency", "currency_code", "currencyCode"):
            raw = text.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip().upper()
        sym = text.get("symbol")
        if isinstance(sym, str) and sym.strip() in _SYMBOL_TO_CURRENCY:
            return _SYMBOL_TO_CURRENCY[sym.strip()]
        return _currency_from_display(text.get("raw") or text.get("displayed_price") or text.get("name"))
    if not isinstance(text, str) or not text.strip():
        return None
    for sym, code in _SYMBOL_TO_CURRENCY.items():
        if sym in text:
            return code
    m = re.search(r"\b([A-Z]{3})\b", text)
    return m.group(1) if m else None


def _price_and_currency(product: dict) -> tuple[float | None, str | None]:
    price_obj = product.get("price")
    if isinstance(price_obj, dict):
        price = _float(price_obj.get("extracted_value") or price_obj.get("value") or price_obj.get("raw"))
        return price, _currency_from_display(price_obj)
    price = _float(product.get("extracted_price") or price_obj)
    return price, _currency_from_display(price_obj)


def _helpful_votes(v: Any) -> int | None:
    if v is None:
        return None
    parsed = _int(v)
    if parsed is not None:
        return parsed
    m = re.search(r"(\d[\d,]*)", str(v))
    if not m:
        return None
    return _int(m.group(1).replace(",", ""))


def _plain(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _looks_like_review_row(row: dict) -> bool:
    if any(isinstance(row.get(k), (str, int, float)) and row.get(k) not in ("", None) for k in ("text", "body", "snippet", "title")):
        return True
    return row.get("rating") is not None and (row.get("author") or row.get("date"))


def _collect_serpapi_review_rows(obj: Any, out: list[dict], depth: int = 0) -> None:
    if depth > 8 or obj is None:
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _REVIEW_LIST_KEYS and isinstance(val, list):
                out.extend(x for x in val if isinstance(x, dict) and _looks_like_review_row(x))
            elif key == "customer_reviews" and isinstance(val, list):
                out.extend(x for x in val if isinstance(x, dict) and _looks_like_review_row(x))
            else:
                _collect_serpapi_review_rows(val, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:80]:
            _collect_serpapi_review_rows(item, out, depth + 1)


def _histogram_rating(info: dict | None) -> tuple[float | None, int | None]:
    if not isinstance(info, dict):
        return None, None
    summary = info.get("summary") if isinstance(info.get("summary"), dict) else info
    cr = summary.get("customer_reviews") if isinstance(summary, dict) else None
    if not isinstance(cr, dict):
        return None, None
    total = 0
    weighted = 0.0
    for stars in range(1, 6):
        n = _int(cr.get(f"{stars} star") or cr.get(str(stars)))
        if not n:
            continue
        total += n
        weighted += n * stars
    if total <= 0:
        return None, None
    return round(weighted / total, 2), total


def _brand_from_product(product: dict, details: dict) -> str | None:
    for src in (product.get("brand"), details.get("brand"), details.get("manufacturer")):
        if isinstance(src, str) and src.strip():
            text = src.strip()
            text = re.sub(r"^Brand:\s*", "", text, flags=re.I)
            visit = re.match(r"^Visit the (.+) Store$", text, flags=re.I)
            if visit:
                text = visit.group(1).strip()
            return text or None
        if isinstance(src, dict):
            name = src.get("name") or src.get("value")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _reviews_from_authorized_html(html: str, asin: str, limit: int) -> list[Review]:
    """Parse review cards from SerpAPI's authorized HTML snapshot (not a direct Amazon scrape)."""
    if not html:
        return []
    blocks = _REVIEW_BLOCK_RE.findall(html)
    if not blocks:
        blocks = [html]
    out: list[Review] = []
    seen: set[str] = set()
    for i, block in enumerate(blocks):
        if len(out) >= limit:
            break
        bodies = [_plain(x) for x in _REVIEW_BODY_RE.findall(block)]
        text = next((b for b in bodies if b and len(b) > 20), None)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        titles = [_plain(x) for x in _REVIEW_TITLE_RE.findall(block)]
        title = next((t for t in titles if t and "out of 5" not in t.lower()), None)
        star_m = _STAR_RE.search(block)
        rating = _float(star_m.group(1)) if star_m else None
        out.append(
            Review(
                platform="amazon",
                product_id=asin,
                review_id=f"{asin}-html-{i+1}",
                rating=rating,
                title=title,
                text=text,
            )
        )
    return out


class AmazonProvider(BaseProvider):
    name = "amazon"
    platform = "amazon"
    display_name = "Amazon"

    def source_label(self) -> str:
        backends = []
        if RAINFOREST_API_KEY:
            backends.append("Rainforest API")
        if SERPAPI_API_KEY:
            backends.append("SerpAPI")
        if self._paapi_configured():
            backends.append("Amazon PA-API")
        if backends:
            return f"{self.display_name} ({', '.join(backends)})"
        return f"{self.display_name} (not configured)"

    def __init__(self) -> None:
        self._serpapi_product_cache: dict[tuple[str, str], dict] = {}

    def supports_url(self, url: str) -> bool:
        return detect_platform(url).platform == "amazon"

    def is_configured(self) -> bool:
        # PA-API credentials alone cannot fetch reviews (or product in this provider).
        return bool(RAINFOREST_API_KEY or SERPAPI_API_KEY)

    def _paapi_configured(self) -> bool:
        return bool(AMAZON_PAAPI_ACCESS_KEY and AMAZON_PAAPI_SECRET_KEY and AMAZON_PAAPI_PARTNER_TAG)

    def health_check(self) -> dict:
        backends = []
        if RAINFOREST_API_KEY:
            backends.append("rainforest")
        if SERPAPI_API_KEY:
            backends.append("serpapi")
        if self._paapi_configured():
            backends.append("amazon_paapi_keys_present_reviews_not_supported")
        return {
            "provider": self.name,
            "configured": self.is_configured(),
            "backends": backends,
            "note": (
                "Amazon Product Advertising API typically does not return customer reviews. "
                "Reviews require Rainforest API or SerpAPI (authorized third-party) when keys are set."
            ),
        }

    def configuration_help(self) -> str:
        return (
            "Set RAINFOREST_API_KEY and/or SERPAPI_API_KEY in .env. "
            "Amazon PA-API credentials alone are not sufficient for reviews."
        )

    def fetch_product(self, url: str) -> Product | None:
        if not self.is_configured():
            return None
        asin = extract_asin(url)
        domain = amazon_domain_from_url(url)
        if SERPAPI_API_KEY and asin:
            product = self._serpapi_product(asin, domain, url)
            if product is not None:
                return product
        if RAINFOREST_API_KEY:
            product = self._rainforest_product(url, asin, domain)
            if product is not None:
                return product
        log.info("Amazon product fetch returned no authorized payload")
        return None

    def fetch_reviews(self, url: str, limit: int | None = None) -> list[Review]:
        if not self.is_configured():
            return []
        cap = limit or MAX_REVIEWS
        asin = extract_asin(url)
        domain = amazon_domain_from_url(url)

        if SERPAPI_API_KEY and asin:
            reviews = self._serpapi_reviews(asin, domain, cap)
            if reviews:
                return reviews
            if RAINFOREST_API_KEY:
                return self._rainforest_reviews(url, asin, domain, cap)
            return []

        if RAINFOREST_API_KEY:
            return self._rainforest_reviews(url, asin, domain, cap)
        return []

    def _rainforest_product(self, url: str, asin: str | None, domain: str) -> Product | None:
        params: dict[str, str] = {
            "api_key": RAINFOREST_API_KEY,
            "type": "product",
        }
        if asin:
            params["asin"] = asin
            params["amazon_domain"] = domain
        else:
            params["url"] = url
        data = self._get_json("https://api.rainforestapi.com/request", params)
        if not data or data.get("request_info", {}).get("success") is False:
            log.info("Rainforest product request unsuccessful")
            return None
        p = data.get("product") or {}
        if not p:
            return None
        rating = None
        rating_count = None
        if isinstance(p.get("rating"), dict):
            rating = _float(p["rating"].get("rating"))
            rating_count = _int(p["rating"].get("ratings_total") or p["rating"].get("count"))
        else:
            rating = _float(p.get("rating"))
            rating_count = _int(p.get("ratings_total") or p.get("rating_count"))
        price = None
        currency = None
        buybox = p.get("buybox_winner") or p.get("price") or {}
        if isinstance(buybox, dict):
            price = _float(buybox.get("value") or buybox.get("raw"))
            currency = _currency_from_display(buybox)
        returned_asin = (p.get("asin") or asin)
        if asin and returned_asin and str(returned_asin).upper() != asin.upper():
            log.info("Rainforest product ASIN did not match requested ASIN")
            return None
        return Product(
            platform="amazon",
            product_id=str(returned_asin).upper() if returned_asin else asin,
            url=p.get("link") or url,
            title=p.get("title"),
            brand=p.get("brand") if isinstance(p.get("brand"), str) else None,
            category=_category(p),
            price=price,
            currency=currency,
            rating=rating,
            rating_count=rating_count,
            review_count=_int(p.get("reviews_total") or p.get("ratings_total")),
            image_url=_first_image(p),
            extra={"marketplace": domain} if domain else {},
        )

    def _rainforest_reviews(self, url: str, asin: str | None, domain: str, limit: int) -> list[Review]:
        out: list[Review] = []
        pid = asin
        for page in (1, 2):
            params: dict[str, str] = {
                "api_key": RAINFOREST_API_KEY,
                "type": "reviews",
            }
            if asin:
                params["asin"] = asin
                params["amazon_domain"] = domain
            else:
                params["url"] = url
            if page > 1:
                params["page"] = str(page)
            data = self._get_json("https://api.rainforestapi.com/request", params)
            if not data:
                break
            pid = asin or (data.get("request_parameters") or {}).get("asin") or pid
            if asin and pid and str(pid).upper() != asin.upper():
                break
            rows = [row for row in (data.get("reviews") or []) if isinstance(row, dict)]
            if not rows:
                break
            before = len(out)
            for row in rows:
                if len(out) >= limit:
                    break
                profile = row.get("profile") or {}
                out.append(
                    Review(
                        platform="amazon",
                        product_id=pid,
                        review_id=row.get("id") or row.get("review_id"),
                        reviewer_id=profile.get("id") or profile.get("link"),
                        reviewer_name=profile.get("name"),
                        rating=_float(row.get("rating")),
                        title=row.get("title"),
                        text=row.get("body") or row.get("text"),
                        helpful_votes=_int(row.get("helpful_votes")),
                        review_date=_parse_date(row.get("date", {}).get("utc") if isinstance(row.get("date"), dict) else row.get("date")),
                        verified_purchase=row.get("verified_purchase") if isinstance(row.get("verified_purchase"), bool) else None,
                    )
                )
            added = len(out) - before
            if len(out) >= limit or added < 10:
                break
        return out

    def _serpapi_product_payload(self, asin: str, domain: str) -> dict | None:
        cache_key = (asin, domain)
        if cache_key in self._serpapi_product_cache:
            return self._serpapi_product_cache[cache_key]
        if len(self._serpapi_product_cache) >= _SERPAPI_CACHE_MAX:
            self._serpapi_product_cache.clear()
        data = self._get_json(
            "https://serpapi.com/search.json",
            {
                "engine": "amazon_product",
                "asin": asin,
                "amazon_domain": domain,
                "api_key": SERPAPI_API_KEY,
            },
        )
        if data is not None and not data.get("error") and _serpapi_payload_has_product(data):
            self._serpapi_product_cache[cache_key] = data
        return data

    def _serpapi_product(self, asin: str, domain: str, url: str) -> Product | None:
        data = self._serpapi_product_payload(asin, domain)
        if not data or data.get("error"):
            log.info("SerpAPI product error: %s", data.get("error") if data else "no response")
            return None
        p = data.get("product_results") or {}
        if not p:
            return None
        details = data.get("product_details") if isinstance(data.get("product_details"), dict) else {}
        cats = p.get("categories") if isinstance(p.get("categories"), list) else []
        category = None
        if cats and isinstance(cats[-1], dict):
            category = cats[-1].get("name")
        price, currency = _price_and_currency(p)
        brand = _brand_from_product(p, details)
        returned_asin = p.get("asin") or asin
        if asin and returned_asin and str(returned_asin).upper() != asin.upper():
            log.info("SerpAPI product ASIN did not match requested ASIN")
            return None
        hist_rating, hist_n = _histogram_rating(data.get("reviews_information") if isinstance(data.get("reviews_information"), dict) else None)
        rating = _float(p.get("rating") or details.get("rating")) or hist_rating
        rating_count = _int(p.get("reviews") or details.get("reviews")) or hist_n
        return Product(
            platform="amazon",
            product_id=str(returned_asin).upper() if returned_asin else asin,
            url=p.get("link") or url,
            title=p.get("title"),
            brand=brand,
            category=category,
            price=price,
            currency=currency,
            rating=rating,
            rating_count=rating_count,
            review_count=rating_count,
            image_url=p.get("thumbnail"),
            extra={"marketplace": domain} if domain else {},
        )

    def _serpapi_reviews(self, asin: str, domain: str, limit: int) -> list[Review]:
        data = self._serpapi_product_payload(asin, domain)
        reviews = self._reviews_from_serpapi_json(asin, data, limit)
        if reviews:
            return reviews
        fresh = self._get_json(
            "https://serpapi.com/search.json",
            {
                "engine": "amazon_product",
                "asin": asin,
                "amazon_domain": domain,
                "no_cache": "true",
                "api_key": SERPAPI_API_KEY,
            },
        )
        if fresh is not None and not fresh.get("error") and _serpapi_payload_has_product(fresh):
            self._serpapi_product_cache[(asin, domain)] = fresh
            reviews = self._reviews_from_serpapi_json(asin, fresh, limit)
            if reviews:
                return reviews
        html = self._get_text(
            "https://serpapi.com/search",
            {
                "engine": "amazon_product",
                "asin": asin,
                "amazon_domain": domain,
                "output": "html",
                "api_key": SERPAPI_API_KEY,
            },
        )
        html_reviews = _reviews_from_authorized_html(html or "", asin, limit)
        if html_reviews:
            log.info("Amazon reviews parsed from authorized SerpAPI HTML snapshot n=%s", len(html_reviews))
        return html_reviews

    def _reviews_from_serpapi_json(self, asin: str, data: dict | None, limit: int) -> list[Review]:
        if not data or data.get("error"):
            return []
        rows: list[dict] = []
        _collect_serpapi_review_rows(data, rows)
        out: list[Review] = []
        seen: set[str] = set()
        for row in rows:
            if len(out) >= limit:
                break
            text = _plain(row.get("text") or row.get("body") or row.get("snippet"))
            title = _plain(row.get("title")) if isinstance(row.get("title"), str) else None
            rating = _float(row.get("rating"))
            if not text and not title and rating is None:
                continue
            key = text or title or str(row.get("position"))
            if key in seen:
                continue
            seen.add(str(key))
            review_id = None
            link = row.get("link") or row.get("author_link")
            if isinstance(link, str) and "/customer-reviews/" in link:
                review_id = link.rstrip("/").split("/")[-1].split("?")[0]
            elif row.get("position") is not None:
                review_id = f"{asin}-{row.get('position')}"
            verified = row.get("verified_purchase")
            if not isinstance(verified, bool):
                verified = row.get("verified") if isinstance(row.get("verified"), bool) else None
            author = row.get("author")
            out.append(
                Review(
                    platform="amazon",
                    product_id=asin,
                    review_id=review_id,
                    reviewer_id=None,
                    reviewer_name=author if isinstance(author, str) else None,
                    rating=rating,
                    title=title,
                    text=text,
                    helpful_votes=_helpful_votes(row.get("helpful_votes")),
                    review_date=_parse_date(row.get("date")),
                    verified_purchase=verified,
                )
            )
        return out

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict | None:
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SEC) as client:
                r = client.get(endpoint, params=params)
                try:
                    data = r.json()
                except Exception:
                    data = None
                if r.status_code >= 400:
                    err = data.get("error") if isinstance(data, dict) else None
                    log.warning(
                        "Authorized API HTTP %s from %s error=%s",
                        r.status_code,
                        endpoint,
                        err or "non-json body",
                    )
                    return None
                return data if isinstance(data, dict) else None
        except Exception:
            log.warning("Authorized API request failed for %s", endpoint)
            return None

    def _get_text(self, endpoint: str, params: dict[str, str]) -> str | None:
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SEC) as client:
                r = client.get(endpoint, params=params)
                if r.status_code >= 400:
                    log.warning("Authorized API HTTP %s from %s", r.status_code, endpoint)
                    return None
                return r.text
        except Exception:
            log.warning("Authorized API text request failed for %s", endpoint)
            return None


def _category(product: dict) -> str | None:
    cats = product.get("categories") or product.get("bestsellers_rank")
    if isinstance(cats, list) and cats:
        first = cats[0]
        if isinstance(first, dict):
            return first.get("name") or first.get("category")
        return str(first)
    return None


def _first_image(product: dict) -> str | None:
    imgs = product.get("images") or []
    if imgs and isinstance(imgs[0], dict):
        return imgs[0].get("link")
    main = product.get("main_image")
    if isinstance(main, dict):
        return main.get("link")
    return product.get("image")
