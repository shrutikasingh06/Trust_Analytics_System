"""FastAPI app: URL in → detect → authorized provider → live trust analytics."""
from __future__ import annotations

import logging
from pathlib import Path

from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.config import PLATFORM_LABELS, SUPPORTED_PLATFORMS
from src.pipeline import analyze_url
from src.platform_detector import detect_platform
from src.providers.provider_factory import list_providers

ROOT = Path(__file__).resolve().parents[1]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("trust_analytics.app")

ASSET_VERSION = "20260821a"

app = FastAPI(title="E-Commerce Trust Analytics", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "web" / "templates"))


@app.middleware("http")
async def disable_stale_page_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class AnalyzeBody(BaseModel):
    url: str = Field(default="", max_length=2048)


_VENDOR_MARKERS = ("serpapi", "rainforest", "pa-api", "paapi", "api_key", "apikey")


def _strip_secrets(obj):
    """Never send credentials or vendor auth details to the browser."""
    banned = {"api_key", "apikey", "access_key", "secret_key", "secret", "token", "password"}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in banned or "api_key" in str(k).lower():
                continue
            out[k] = _strip_secrets(v)
        return out
    if isinstance(obj, list):
        return [_strip_secrets(x) for x in obj]
    if isinstance(obj, str):
        lower = obj.lower()
        if "api_key=" in lower or any(m in lower for m in _VENDOR_MARKERS):
            return "[redacted]"
    return obj


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "asset_version": ASSET_VERSION,
            "supported": sorted(
                PLATFORM_LABELS[k] for k, v in SUPPORTED_PLATFORMS.items() if v
            )
        },
    )
    response.headers["Cache-Control"] = "no-store, no-cache, max-age=0, must-revalidate"
    return response


@app.get("/health")
async def health():
    return _strip_secrets({"ok": True, "providers": list_providers()})


@app.get("/api/platforms")
async def api_platforms():
    """Catalog of supported storefronts. configured ≠ LIVE (LIVE requires a successful fetch)."""
    providers = list_providers()
    rows = []
    for key, enabled in SUPPORTED_PLATFORMS.items():
        info = providers.get(key) or {}
        rows.append(
            {
                "platform": key,
                "platform_label": PLATFORM_LABELS.get(key, key),
                "enabled_in_config": enabled,
                "provider": info.get("provider"),
                "provider_configured": bool(info.get("configured")),
                "live": False,
                "catalog_api": info.get("catalog_api"),
                "review_api": info.get("review_api"),
                "credential_required": info.get("credential_required"),
                "where_to_get_it": info.get("where_to_get_it"),
                "cost": info.get("cost"),
                "catalog_available_now": info.get("catalog_available_now"),
                "reviews_available_now": info.get("reviews_available_now"),
                "note": "LIVE is set only after a successful live review fetch for a URL, not from this catalog.",
            }
        )
    return {"platforms": rows}


@app.post("/api/detect")
async def api_detect(body: AnalyzeBody):
    d = detect_platform(body.url)
    return {
        "platform": d.platform,
        "platform_label": d.platform_label,
        "valid_url": d.valid_url,
        "enabled": d.enabled,
        "host": d.host,
        "reason": d.reason,
    }


@app.post("/api/analyze")
async def api_analyze(body: AnalyzeBody):
    raw_url = (body.url or "").strip()
    parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
    log.info("POST /api/analyze host=%s path=%s", parsed.netloc or "-", (parsed.path or "/")[:80])
    try:
        result = analyze_url(raw_url)
        payload = _strip_secrets(result.model_dump(mode="json"))
        payload.pop("weighting_notes", None)
        return JSONResponse(payload)
    except Exception:
        log.exception("Unhandled analyze error")
        return JSONResponse(
            {
                "status": "DATA_ACCESS_UNAVAILABLE",
                "data_status": "DATA_ACCESS_UNAVAILABLE",
                "platform": None,
                "platform_label": None,
                "provider": None,
                "source": None,
                "provider_configured": False,
                "data_mode": "UNAVAILABLE",
                "product": None,
                "reviews": [],
                "field_availability": None,
                "trust_analysis": None,
                "user_message": "Unable to retrieve live review data right now. Please try again.",
                "required_action": "Please try again.",
            },
            status_code=200,
        )
