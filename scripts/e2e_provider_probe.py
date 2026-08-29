"""Honest live-provider probe. Reports only what actually returned data. Never fabricates success."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import (  # noqa: E402
    FLIPKART_AFFILIATE_ID,
    FLIPKART_AFFILIATE_TOKEN,
    RAINFOREST_API_KEY,
    SERPAPI_API_KEY,
)
from src.pipeline import analyze_url  # noqa: E402
from src.providers.provider_factory import list_providers  # noqa: E402

# Honest live-provider probe across supported storefronts.
# Amazon is included only as a temporary integration test when SerpAPI/Rainforest is configured.
# Other platforms must report NOT_CONFIGURED / DATA_ACCESS_UNAVAILABLE until a legitimate API exists.
AMAZON_SAMPLE = "https://www.amazon.com/dp/B072MQ5BRX"
FLIPKART_SAMPLE = "https://www.flipkart.com/example/p/itm123?pid=TESTPID"


def main() -> int:
    report = {
        "configured_backends": {
            "rainforest": bool(RAINFOREST_API_KEY),
            "serpapi": bool(SERPAPI_API_KEY),
            "flipkart_affiliate": bool(FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN),
        },
        "provider_health": list_providers(),
        "probes": [],
    }

    probes = [
        ("amazon", AMAZON_SAMPLE, RAINFOREST_API_KEY or SERPAPI_API_KEY),
        ("flipkart", FLIPKART_SAMPLE, FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN),
        ("myntra", "https://www.myntra.com/dresses/example", False),
        ("nykaa", "https://www.nykaa.com/example", False),
        ("meesho", "https://www.meesho.com/example", False),
        ("ajio", "https://www.ajio.com/example", False),
        ("croma", "https://www.croma.com/example", False),
        ("reliance_digital", "https://www.reliancedigital.in/example", False),
        ("tata_cliq", "https://www.tatacliq.com/example", False),
    ]

    any_live = False
    for name, url, should_attempt in probes:
        result = analyze_url(url)
        entry = {
            "probe": name,
            "url": url,
            "attempted_live_fetch": bool(should_attempt),
            "status": result.status,
            "platform": result.platform,
            "provider": result.provider,
            "provider_configured": result.provider_configured,
            "n_reviews": len(result.reviews),
            "has_product": result.product is not None,
            "data_mode": result.data_mode,
            "data_status": result.data_status,
            "source": result.source,
        }
        if result.status in {"LIVE", "PARTIAL"} and result.reviews:
            any_live = True
            entry["live_success"] = True
        else:
            entry["live_success"] = False
        report["probes"].append(entry)

    report["any_platform_returned_live_reviews"] = any_live
    report["statement"] = (
        "At least one configured provider returned live reviews."
        if any_live
        else "No platform returned live reviews in this run. Do not claim any storefront is LIVE."
    )

    out = ROOT / "reports" / "provider_e2e_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Wrote {out}")
    return 0 if report["probes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
