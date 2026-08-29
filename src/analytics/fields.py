"""Inspect which normalized fields are actually present. Never impute."""
from __future__ import annotations

from src.schemas import FieldAvailability, Product, Review

PRODUCT_FIELDS = [
    "platform",
    "product_id",
    "url",
    "title",
    "brand",
    "category",
    "price",
    "rating",
    "rating_count",
    "review_count",
    "image_url",
]

REVIEW_FIELDS = [
    "platform",
    "product_id",
    "review_id",
    "reviewer_id",
    "reviewer_name",
    "rating",
    "title",
    "text",
    "helpful_votes",
    "review_date",
    "verified_purchase",
]


def product_availability(product: Product | None) -> FieldAvailability:
    if product is None:
        return FieldAvailability(available=[], unavailable=PRODUCT_FIELDS)
    available, unavailable = [], []
    dump = product.model_dump()
    for key in PRODUCT_FIELDS:
        if dump.get(key) is None:
            unavailable.append(f"product.{key}")
        else:
            available.append(f"product.{key}")
    return FieldAvailability(available=available, unavailable=unavailable)


def review_availability(reviews: list[Review]) -> FieldAvailability:
    available, unavailable = [], []
    if not reviews:
        return FieldAvailability(
            available=[],
            unavailable=[f"review.{k}" for k in REVIEW_FIELDS],
        )
    dumps = [r.model_dump() for r in reviews]
    n = len(dumps)
    for key in REVIEW_FIELDS:
        present = sum(1 for d in dumps if d.get(key) is not None)
        if present == 0:
            unavailable.append(f"review.{key}")
        else:
            available.append(f"review.{key} ({present}/{n})")
    return FieldAvailability(available=available, unavailable=unavailable)


def merge_availability(*parts: FieldAvailability) -> FieldAvailability:
    avail, unavail = [], []
    for p in parts:
        avail.extend(p.available)
        unavail.extend(p.unavailable)
    return FieldAvailability(available=avail, unavailable=unavail)
