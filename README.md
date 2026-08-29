# E-Commerce Trust Analytics

Platform-independent trust / risk analytics for **live** product reviews from authorized data providers.

This is not an HTML scraper and not a single-storefront product. Amazon, Flipkart, Myntra, Nykaa, Meesho, Ajio, Croma, Reliance Digital, and Tata CLiQ are **data sources**. Detection, feature engineering, scoring, and the dashboard stay the same when you add `src/providers/new_platform_provider.py` and register it in `provider_factory.py`.

## User flow

Paste any supported product URL → detect platform → select that platform’s provider → fetch live product/reviews if authorized access exists → normalize to the common schema → trust analysis → explainable dashboard.

## Live vs training data

| Data | Role |
|---|---|
| `dataset/csv_files/` and cleaned parquet (including `review_features.parquet`) | EDA, feature engineering research, model **development**. Never shown as the pasted URL’s reviews. |
| Provider `fetch_product` / `fetch_reviews` | Only source for the product the user pasted. |

Statuses:

- **LIVE** — authorized provider exists, credentials are configured, and a live product/review request succeeds.
- **NOT_CONFIGURED** — provider/credentials are unavailable.
- **DATA_ACCESS_UNAVAILABLE** — the URL is recognized but authorized live review data cannot be obtained.

The system does not invent reviews, ratings, or trust scores, and does not substitute historical rows.

## Current provider reality

Listing a platform does **not** mean it is LIVE.

| Platform | Detection | Provider module | Live (this project, as of last probe) |
|---|---|---|---|
| Amazon | Yes | `amazon_provider.py` | Temporarily validated via SerpAPI when a key is set and a fetch succeeds |
| Flipkart | Yes | `flipkart_provider.py` | Awaiting a review-capable authorized API |
| Myntra | Yes | `myntra_provider.py` | Awaiting authorized API |
| Nykaa | Yes | `nykaa_provider.py` | Awaiting authorized API |
| Meesho | Yes | `meesho_provider.py` | Awaiting authorized API |
| Ajio | Yes | `ajio_provider.py` | Awaiting authorized API |
| Croma | Yes | `croma_provider.py` | Awaiting authorized API |
| Reliance Digital | Yes | `reliance_digital_provider.py` | Awaiting authorized API |
| Tata CLiQ | Yes | `tata_cliq_provider.py` | Awaiting authorized API |

Amazon is a **temporary integration test**, not the product focus.

## Run the dashboard

```bash
pip install -r requirements-app.txt
copy .env.example .env
uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Configure authorized providers

See `.env.example`. Each platform has its own env vars. Empty keys mean **NOT_CONFIGURED**.

## Tests

```bash
python -m pytest -q
python scripts/e2e_provider_probe.py
```

Treat a storefront as LIVE only if the probe shows `live_success: true` for that platform.

## Scoring language

Scores are transparent adjustments from a neutral prior of 50 using **normalized live-sample features** (duplicates, rating concentration, reviewer diversity when IDs exist, etc.). They do not depend on which storefront produced the reviews. They are **not** “fake review detection accuracy”.
