"""Extract store-specific product identifiers from product URLs. No cross-store mapping."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from src.product_ids import extract_catalog_product_id

_PID = re.compile(r"[?&]pid=([A-Z0-9]+)", re.I)
_MYNTRA_BUY = re.compile(r"/(\d{5,})/buy(?:/|$|\?)", re.I)
_P_NUM = re.compile(r"/p/(\d{3,})(?:/|$|\?)", re.I)
_P_TOKEN = re.compile(r"/p/([A-Za-z0-9_-]{4,})(?:/|$|\?)", re.I)
_AJIO_STYLE = re.compile(r"/p/([0-9]{6,}(?:_[a-z0-9]+)?)", re.I)
_TATA = re.compile(r"/(?:p-mp|c-mp)([a-z0-9-]+)", re.I)
_MEESHO = re.compile(r"/p/([a-z0-9]{4,})", re.I)


def extract_store_product_id(platform: str, url: str) -> str | None:
    text = url or ""
    if platform == "amazon":
        return extract_catalog_product_id("amazon", text)
    if platform == "flipkart":
        m = _PID.search(text)
        return m.group(1).upper() if m else None
    parsed = urlparse(text if "://" in text else f"https://{text}")
    path = parsed.path or ""
    query = parse_qs(parsed.query)
    if platform == "myntra":
        m = _MYNTRA_BUY.search(path)
        if m:
            return m.group(1)
        parts = [p for p in path.split("/") if p.isdigit() and len(p) >= 5]
        return parts[-1] if parts else None
    if platform == "nykaa":
        m = _P_NUM.search(path)
        return m.group(1) if m else None
    if platform == "meesho":
        m = _MEESHO.search(path)
        return m.group(1) if m else None
    if platform == "ajio":
        m = _AJIO_STYLE.search(path) or _P_TOKEN.search(path)
        return m.group(1) if m else None
    if platform in {"croma", "reliance_digital"}:
        m = _P_NUM.search(path)
        if m:
            return m.group(1)
        parts = [p for p in path.split("/") if p]
        last = parts[-1] if parts else ""
        tail = re.search(r"-(\d{5,})$", last)
        if tail:
            return tail.group(1)
        if parts and parts[0].lower() == "product" and last:
            return last
        m = _P_TOKEN.search(path)
        return m.group(1) if m else None
    if platform == "tata_cliq":
        m = _TATA.search(path)
        if m:
            return "mp" + m.group(1)
        for key in ("productId", "pid", "sku"):
            vals = query.get(key) or query.get(key.lower())
            if vals and vals[0]:
                return vals[0]
        m = _P_TOKEN.search(path)
        return m.group(1) if m else None
    return None
