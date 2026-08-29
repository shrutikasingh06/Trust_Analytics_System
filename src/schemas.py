from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Product(BaseModel):
    platform: str
    product_id: str | None = None
    url: str
    title: str | None = None
    brand: str | None = None
    category: str | None = None
    price: float | None = None
    currency: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    review_count: int | None = None
    image_url: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Review(BaseModel):
    platform: str
    product_id: str | None = None
    review_id: str | None = None
    reviewer_id: str | None = None
    reviewer_name: str | None = None
    rating: float | None = None
    title: str | None = None
    text: str | None = None
    helpful_votes: int | None = None
    review_date: datetime | None = None
    verified_purchase: bool | None = None


class FieldAvailability(BaseModel):
    available: list[str]
    unavailable: list[str]


class RiskFactor(BaseModel):
    direction: Literal["positive", "negative", "neutral"]
    label: str
    detail: str
    feature: str
    value: float | int | str | None = None


class ReviewFlags(BaseModel):
    review_id: str | None
    rating: float | None
    flags: list[str]
    risk_band: Literal["Low", "Medium", "High"]


class TrustAnalysis(BaseModel):
    product_trust_score: int | None
    review_risk_band: Literal["Low", "Medium", "High", "Unknown"]
    n_reviews_used: int
    data_mode: Literal["LIVE", "HISTORICAL"]
    historical_training_data_used_as_product_reviews: bool = False
    metrics: dict[str, float | int | None]
    contributing_factors: list[RiskFactor]
    rating_distribution: dict[str, int]
    review_flags: list[ReviewFlags]
    notes: list[str]


class AnalysisResult(BaseModel):
    status: Literal["LIVE", "PARTIAL", "HISTORICAL", "CATALOG_ONLY", "DATA_ACCESS_UNAVAILABLE", "UNSUPPORTED_PLATFORM", "INVALID_URL"]
    data_status: Literal[
        "LIVE",
        "HISTORICAL",
        "LIVE_AND_HISTORICAL",
        "CATALOG_ONLY",
        "NOT_CONFIGURED",
        "DATA_ACCESS_UNAVAILABLE",
        "UNSUPPORTED_PLATFORM",
        "INVALID_URL",
    ]
    platform: str | None
    platform_label: str | None
    provider: str | None
    source: str | None = None
    provider_configured: bool
    data_mode: Literal["LIVE", "HISTORICAL", "LIVE_AND_HISTORICAL", "CATALOG", "UNAVAILABLE", "NONE"]
    product: Product | None = None
    reviews: list[Review] = Field(default_factory=list)
    field_availability: FieldAvailability | None = None
    trust_analysis: TrustAnalysis | None = None
    user_message: str
    required_action: str | None = None
    review_count: int | None = None
    product_trust_score: int | None = None
    trust_level: Literal["High", "Moderate", "Low"] | None = None
    review_summary: dict[str, Any] | None = None
    sentiment_summary: dict[str, Any] | None = None
    suspicious_review_summary: dict[str, Any] | None = None
    reviewer_summary: dict[str, Any] | None = None
    explanations: list[dict[str, Any]] = Field(default_factory=list)
    review_analyses: list[dict[str, Any]] = Field(default_factory=list)
    reviewer_analyses: list[dict[str, Any]] = Field(default_factory=list)
    trust_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    weighting_notes: list[str] = Field(default_factory=list)
    historical_analysis: dict[str, Any] | None = None
    catalog_product_id: str | None = None
