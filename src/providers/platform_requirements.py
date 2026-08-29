"""Exact catalog/review backends for each storefront. No invented vendors."""
from __future__ import annotations

# Catalog APIs that this codebase can call. Review APIs that return customer text.
# "none_public" means no official/public review API is documented for arbitrary product URLs.

REQUIREMENTS: dict[str, dict] = {
    "amazon": {
        "catalog_api": "Rainforest type=product and/or SerpAPI engine=amazon_product",
        "review_api": "Rainforest type=reviews and/or SerpAPI engine=amazon_product (review bodies on the product payload)",
        "credential": "RAINFOREST_API_KEY (alias AMAZON_PROVIDER_API_KEY) and/or SERPAPI_API_KEY",
        "where": "https://www.rainforestapi.com/ and/or https://serpapi.com/",
        "cost": "Paid (both vendors). Amazon PA-API is catalog-only and is not used for reviews.",
        "catalog_available_now": True,
        "reviews_available_now": True,
    },
    "flipkart": {
        "catalog_api": "Flipkart Affiliate Product API (GET /affiliate/1.0/product.json?id={pid})",
        "review_api": "None public. Affiliate API has no review text. SerpAPI has no Flipkart engine (roadmap issue #2222, frozen).",
        "credential": "FLIPKART_AFFILIATE_ID + FLIPKART_AFFILIATE_TOKEN for catalog. No review credential exists to wire.",
        "where": "https://affiliate.flipkart.com/",
        "cost": "Affiliate program is free after approval. No paid official review API is offered.",
        "catalog_available_now": True,
        "reviews_available_now": False,
    },
    "myntra": {
        "catalog_api": "None public for shopper product URLs. Seller/partner APIs are not a URL-in review feed.",
        "review_api": "None public. Requires a Myntra official partner API that returns customer review text (not currently offered for this app).",
        "credential": "MYNTRA_PROVIDER_API_KEY (placeholder until Myntra grants a review-capable API)",
        "where": "No public developer signup. Myntra corporate/partner engineering only.",
        "cost": "Unknown / contract only",
        "catalog_available_now": False,
        "reviews_available_now": False,
    },
    "nykaa": {
        "catalog_api": "None public for shopper product URLs.",
        "review_api": "None public. Requires a Nykaa official partner API that returns customer review text.",
        "credential": "NYKAA_PROVIDER_API_KEY (placeholder until Nykaa grants a review-capable API)",
        "where": "No public developer signup. Nykaa partner/API program only.",
        "cost": "Unknown / contract only",
        "catalog_available_now": False,
        "reviews_available_now": False,
    },
    "meesho": {
        "catalog_api": "None public for shopper product URLs. Supplier APIs are seller-ops, not review text.",
        "review_api": "None public. Requires a Meesho official partner API that returns customer review text.",
        "credential": "MEESHO_PROVIDER_API_KEY (placeholder until Meesho grants a review-capable API)",
        "where": "No public developer signup. Meesho partner program only.",
        "cost": "Unknown / contract only",
        "catalog_available_now": False,
        "reviews_available_now": False,
    },
    "ajio": {
        "catalog_api": "None public for shopper product URLs.",
        "review_api": "None public. Requires an Ajio / Reliance Retail official partner API that returns customer review text.",
        "credential": "AJIO_PROVIDER_API_KEY (placeholder until Ajio grants a review-capable API)",
        "where": "No public developer signup. Reliance Retail partner channels only.",
        "cost": "Unknown / contract only",
        "catalog_available_now": False,
        "reviews_available_now": False,
    },
    "croma": {
        "catalog_api": "None public for shopper product URLs.",
        "review_api": "None public. Requires a Croma official partner API that returns customer review text.",
        "credential": "CROMA_PROVIDER_API_KEY (placeholder until Croma grants a review-capable API)",
        "where": "No public developer signup.",
        "cost": "Unknown / contract only",
        "catalog_available_now": False,
        "reviews_available_now": False,
    },
    "reliance_digital": {
        "catalog_api": "None public for shopper product URLs.",
        "review_api": "None public. Requires a Reliance Digital official partner API that returns customer review text.",
        "credential": "RELIANCE_DIGITAL_PROVIDER_API_KEY (placeholder until Reliance Digital grants a review-capable API)",
        "where": "No public developer signup.",
        "cost": "Unknown / contract only",
        "catalog_available_now": False,
        "reviews_available_now": False,
    },
    "tata_cliq": {
        "catalog_api": "None public for shopper product URLs.",
        "review_api": "None public. Requires a Tata CLiQ official partner API that returns customer review text.",
        "credential": "TATA_CLIQ_PROVIDER_API_KEY (placeholder until Tata CLiQ grants a review-capable API)",
        "where": "No public developer signup. Tata CLiQ affiliate/partner channels do not publish review text APIs.",
        "cost": "Unknown / contract only",
        "catalog_available_now": False,
        "reviews_available_now": False,
    },
}


def for_platform(platform: str) -> dict:
    row = REQUIREMENTS.get(platform)
    if not row:
        return {}
    return {
        "catalog_api": row["catalog_api"],
        "review_api": row["review_api"],
        "credential_required": row["credential"],
        "where_to_get_it": row["where"],
        "cost": row["cost"],
        "catalog_available_now": row["catalog_available_now"],
        "reviews_available_now": row["reviews_available_now"],
    }
