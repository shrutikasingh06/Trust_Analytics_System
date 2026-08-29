"""
Structured output models for the platform-independent trust engine.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExplanationItem(BaseModel):
    feature: str
    observed_value: float | int | str | None
    interpretation: str
    direction: Literal["positive", "negative", "neutral"]
    contribution: float | None = None
    component: str | None = None


class ReviewAnalysis(BaseModel):
    review_id: str | None
    rating: float | None
    rating_polarity: str | None
    text_present: bool
    review_length_chars: int | None
    review_length_words: int | None
    helpful_votes: int | None
    sentiment_label: str | None
    sentiment_score: float | None
    suspicion_risk: int
    risk_level: Literal["Low", "Medium", "High"]
    reasons: list[str]
    explanations: list[ExplanationItem]


class ReviewerAnalysis(BaseModel):
    reviewer_id: str
    review_count: int
    unique_products: int | None
    avg_rating: float | None
    rating_std: float | None
    five_star_ratio: float | None
    review_span_days: float | None
    reviews_per_active_day: float | None
    reviewer_trust_score: int | None
    risk_level: Literal["Low", "Medium", "High", "Unknown"]
    reasons: list[str]
    missing_signals: list[str]


class ComponentScore(BaseModel):
    name: str
    weight: float
    applied_weight: float | None
    score: float | None
    available: bool
    note: str


class TrustEngineResult(BaseModel):
    product_trust_score: int | None
    trust_level: Literal["High", "Moderate", "Low"] | None
    suspicion_risk: int | None
    suspicion_level: Literal["Low", "Medium", "High", "Unknown"] | None
    n_reviews: int
    review_analyses: list[ReviewAnalysis] = Field(default_factory=list)
    reviewer_analyses: list[ReviewerAnalysis] = Field(default_factory=list)
    review_summary: dict[str, Any] = Field(default_factory=dict)
    sentiment_summary: dict[str, Any] = Field(default_factory=dict)
    suspicious_review_summary: dict[str, Any] = Field(default_factory=dict)
    reviewer_summary: dict[str, Any] = Field(default_factory=dict)
    breakdown: list[ComponentScore] = Field(default_factory=list)
    explanations: list[ExplanationItem] = Field(default_factory=list)
    weighting_notes: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)
