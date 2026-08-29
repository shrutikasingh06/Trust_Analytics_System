# Provider integration readiness

Date: 2026-08-18  
Probe: `reports/provider_e2e_report.json`  
Rule: a platform is **LIVE** only if a configured provider actually returned reviews for a pasted URL. Amazon may appear LIVE in a probe when SerpAPI is configured; that is a **temporary integration test**, not the product focus. Other storefronts remain NOT_CONFIGURED until a legitimate review API is plugged into the existing provider interface.

No datasets were regenerated. Historical parquet remains training/EDA only.

## Capability matrix (this machine)

| Platform | URL detection | Provider implementation | API configured | Product data available | Review data available | Live test status |
|---|---|---|---|---|---|---|
| Amazon | Yes | `amazon_provider.py` (Rainforest, SerpAPI; PA-API keys recognized, reviews not fetched via PA-API) | No | Code path exists; not exercised | Code path exists; not exercised | **Not LIVE** — `DATA_ACCESS_UNAVAILABLE` |
| Flipkart | Yes | `flipkart_provider.py` (Affiliate Product API) | No | Code path exists; needs `pid=` + affiliate creds | **No** — affiliate API has no review text | **Not LIVE** |
| Myntra | Yes | Interface only | No | No | No | **Not LIVE** |
| Nykaa | Yes | Interface only | No | No | No | **Not LIVE** |
| Meesho | Yes | Interface only | No | No | No | **Not LIVE** |
| Ajio | Yes | Interface only | No | No | No | **Not LIVE** |
| Croma | Yes | Interface only | No | No | No | **Not LIVE** |
| Reliance Digital | Yes | Interface only | No | No | No | **Not LIVE** |
| Tata CLiQ | Yes | Interface only | No | No | No | **Not LIVE** |

Generic JSON-LD (`GENERIC_JSONLD_ENABLED`) is disabled by default and does not return reviews.

## API / provider strategy

| Source | Product metadata | Review text | Key required | Paid | Notes |
|---|---|---|---|---|---|
| Amazon PA-API 5 | Yes (Associates) | **No** (reviews removed years ago) | Access key, secret, partner tag | Associates eligibility | Not sufficient for trust analytics |
| Rainforest API | Yes (`type=product`) | Yes (`type=reviews`) | `RAINFOREST_API_KEY` | Yes (credit plans; hobbyist is small) | **Best practical fit** — already implemented |
| SerpAPI Amazon | Yes (`engine=amazon_product`) | Yes (`engine=amazon_reviews`) | `SERPAPI_API_KEY` | Yes | Coded as Amazon fallback |
| Flipkart Affiliate | Yes (product by id) | **No** | Id + token | Affiliate program | Product-only; analysis still blocked without reviews |
| Flipkart Seller / Marketplace | Seller catalog/orders | No public customer-review feed | Seller account | N/A | Wrong audience (seller ops, not shopper URL) |
| Myntra / Nykaa / Meesho / Ajio / Croma / RD / Tata CLiQ official public review APIs | Not found as documented public review APIs for arbitrary product URLs | — | — | — | Keep unconfigured until a legitimate contract exists |

Do not add random RapidAPI scrapers solely to look complete. Do not HTML-scrape storefronts.

## Recommended next integration (awaiting approval)

1. Obtain a **Rainforest API** key (or SerpAPI if already subscribed).
2. Put it in `.env` as `RAINFOREST_API_KEY=` (alias `AMAZON_PROVIDER_API_KEY=` also accepted).
3. Run `python scripts/e2e_provider_probe.py`.
4. Only then treat Amazon as LIVE if `live_success` is true.

Flipkart/Myntra/etc. stay unconfigured for **review** analysis even if product APIs exist.
