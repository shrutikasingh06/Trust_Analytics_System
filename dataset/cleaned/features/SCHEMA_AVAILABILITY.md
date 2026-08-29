# Schema availability — Trust Feature Engineering

Inspected parquet/manifests only. No columns were assumed.

Corpus used for review/reviewer/product features: `behavior_eda/reviews_combined_dedup.parquet` (21,897,475 reviews) and `behavior_eda/reviewers.parquet` (8,789,960 reviewers).

`candidate_label/` is a **separate experimental extract** (`separate.csv`), not the 21.9M behavior corpus.

| Concept | Status | Where / notes |
|---|---|---|
| Review ID | **AVAILABLE** | `mongo_id` on Track 1 combined parquet (Mongo `$oid`). Used as `review_id`. Not present on `separate.csv` / `ml_ready.parquet`. |
| Reviewer ID | **AVAILABLE** | `reviewerID` on all cleaned review tables and `reviewers.parquet`. |
| Product ASIN | **AVAILABLE** | `asin` |
| Review text | **AVAILABLE** | `reviewText` (plus `text_length`, `text_missing`). Not copied into the review-feature table; used to derive length/duplicate flags. |
| Rating | **AVAILABLE** | `overall` (1–5). Feature name: `rating`. |
| Helpful votes | **AVAILABLE** | `helpful_votes`, `helpful_total`, `helpful_ratio` |
| Review date/time | **AVAILABLE** | `unixReviewTime`, `review_datetime_utc`. Audit showed **day-level** Unix timestamps (00:00:00 UTC), so **hour is not informative**. |
| Category | **AVAILABLE** | `category` |
| Summary | **AVAILABLE** | `summary`, `summary_missing` |
| `class` / rating polarity | **DERIVABLE** on Track 1 | Track 1 stored `rating_polarity` (4–5 vs 1–3), which **is a recode of `overall`**, not trust. Track 2 has `class_raw` mostly missing. **Not used as a trust label.** |
| Candidate label | **AVAILABLE only in Track 2** | `candidate_label` on `candidate_label/*.parquet`. **UNAVAILABLE** on the 21.9M behavior corpus. **Not verified fraud ground truth** — heuristic **candidate suspicious-review label**. |
| BFR features | **AVAILABLE only in Track 2** | `bfr_CS` … `bfr_PC` on `reviews_analysis.parquet`. **UNAVAILABLE** on Track 1. **Excluded from feature tables** (association with candidate label; leakage risk). |
| Verified purchase | **UNAVAILABLE** | No such column in any inspected parquet. |
| Reviewer display name | **AVAILABLE** (EDA only) | `reviewerName` on Track 1; not used as a trust feature. |

## Derivable (computed in this phase)

- Review/summary lengths, calendar fields, product/reviewer rating deviations, exact-text copy counts (hash-based)
- Reviewer ratios/entropy from rating histogram
- Product star-share, velocity, reviewer-mix aggregates
