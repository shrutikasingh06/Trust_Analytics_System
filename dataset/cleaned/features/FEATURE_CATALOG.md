# Trust Feature Catalog

Feature tables are derived from the Track 1 **behavior/EDA** corpus.

**Track 2 `candidate_label` is not verified fraud ground truth.** Call it a **heuristic candidate suspicious-review label**. It lives in a different file (`separate.csv` extract), has ~2% positives, and is strongly associated with `BehaviouralFeatureResult` metrics (e.g. univariate AUC ~0.86 for `bfr_CS` / `bfr_MNR`). Labels may include false positives and false negatives. **Do not train a “fake review” model and report trust accuracy on this target without independent validation.** BFR columns are **not** included in these feature tables.

No sentiment model was run. `rating_polarity` / `class` is star-rating polarity, not trust.

`review_hour` is `hour(review_datetime_utc)` in DuckDB. Source Unix times are calendar-day resolution (midnight UTC). Extracting hour in a local session timezone (this machine: IST) yields **5 for every row**, not a real time-of-day. Do not treat `review_hour` as an informative behavioral feature.

---

## Review-level (`review_features.parquet`)

| Feature | Definition / formula | Source | Trust/risk rationale | Limitations | Leakage |
|---|---|---|---|---|---|
| review_id | Mongo oid | mongo_id | Join key | Import artifact, not predictive | No |
| reviewerID | Reviewer identifier | reviewerID | Join | Amazon id, not a score | No |
| asin | Product identifier | asin | Join | | No |
| category | Amazon category after de-overlap keep-first | category | Context | Cross-listed products kept one category | No |
| rating | Star rating 1–5 | overall | Extremes can be spam-like **or** genuine | Not a trust label | Do not use as target named “trust” |
| helpful_votes | Helpful yes-count | helpful_votes | Low/high helpfulness is an exposure-biased quality signal | Not ground-truth trust | No (unless target is helpfulness) |
| helpful_total | Votes cast | helpful_total | Denominator | | No |
| helpful_ratio | votes/total if total>0 | helpful_ratio | Same | Undefined if total=0 | No |
| review_length_chars | Character length | text_length | Very short/long or templated text | Encoding/language | No |
| review_length_words | Whitespace-split token count; 0 if text missing | reviewText | Same | Naive tokenizer | No |
| summary_length | Characters in summary; 0 if missing | summary | Thin summaries | | No |
| text_available | text_missing=0 | text_missing | Missing text reduces NLP usefulness | Rare | No |
| summary_available | summary_missing=0 | summary_missing | | | No |
| text_very_short | length&lt;5 and text present | text_very_short | Possible junk | Threshold arbitrary | No |
| unixReviewTime | Unix seconds | unixReviewTime | Burst timing needs reviewer join | Day resolution | No |
| review_datetime_utc | UTC timestamp | review_datetime_utc | | Hour not informative | No |
| review_year/month/day_of_week/hour | Calendar parts | review_datetime_utc | Seasonality / burst days | **hour ≈ always 0**; dayofweek is DuckDB Sunday=0 | No |
| product_rating_deviation | rating − mean(rating) of same asin | overall, asin | Outlier vs product consensus | Needs enough reviews per product; not fraud GT | If a product-level **label** were built from the same ratings, careful with circularity. No candidate_label here. |
| reviewer_rating_deviation | rating − mean(rating) of same reviewer | overall, reviewerID | Inconsistent rater vs own mean | n=1 ⇒ deviation 0 | Same |
| exact_text_copy_count | Count of reviews with the same text hash | hash(reviewText) | Copied/spam templates | **Hash collisions possible**; ignores near-duplicates | No |
| is_exact_duplicate_text | copy_count &gt; 1 | derived | Repeated exact text | Same | No |

**Not included:** `candidate_label`, `class_raw`, all `bfr_*`, full `reviewText` (kept in source parquet only).

---

## Reviewer-level (`reviewer_features.parquet`)

Starts from `behavior_eda/reviewers.parquet`, plus rating histogram from the combined corpus.

| Feature | Definition / formula | Source | Trust/risk rationale | Limitations | Leakage |
|---|---|---|---|---|---|
| reviewerID | Key | reviewerID | | | No |
| review_count | Number of reviews | count(*) | Mass-review accounts | Heavy reviewers can be genuine enthusiasts | No |
| unique_products | Distinct asin | asin | Breadth vs focus | | No |
| unique_categories | Distinct category | category | Category hopping | Distorted by keep-first de-overlap | No |
| avg_rating | Mean overall | overall | All-5 or all-1 patterns | | No |
| rating_std | Sample stddev | overall | **NULL if review_count=1** (correct) | Do not fill with 0 blindly | No |
| rating_variance | rating_std² | rating_std | Same; NULL when std NULL | | No |
| rating_min/max/range | Extrema | overall | | | No |
| review_text_count | Rows with text | text_missing | Empty-text reviewers | | No |
| avg/median_review_length | On text_length | text_length | Template length | | No |
| helpful_votes_total | Sum of helpful_votes | helpful_votes | Popularity ≠ trust | | No |
| average_helpfulness_per_review | helpful_votes_total / review_count | derived | | | No |
| first/last_review_time, unix | Min/max timestamps | unixReviewTime | Tenure | | No |
| review_span_days | (last−first)/86400 | unix | 0 if all same day | | No |
| inclusive_active_days | span_days+1 | derived | Avoid divide-by-zero | Calendar approximation | No |
| reviews_per_active_day | review_count / inclusive_active_days | derived | Burstiness | Same-day dump ⇒ high rate | No |
| products_per_category | unique_products / unique_categories | derived | | | No |
| n_rating_1 … n_rating_5 | Histogram | overall | | | No |
| five_star_ratio, one_star_ratio | n_k / review_count | overall | Polarized reviewers | | No |
| extreme_rating_ratio | (n_1+n_5)/n | overall | Extreme-only raters | Common on Amazon | No |
| rating_entropy | −Σ p_k ln p_k over k with p_k&gt;0 | histogram | Low entropy = concentrated stars | Natural log; not bits | No |

`verified_purchase_count`: **unavailable**.

---

## Product-level (`product_features.parquet`)

| Feature | Definition / formula | Source | Trust/risk rationale | Limitations | Leakage |
|---|---|---|---|---|---|
| asin | Key | asin | | | No |
| review_count | Reviews of product | count | Thin vs thick evidence | | No |
| unique_reviewers | Distinct reviewerID | reviewerID | Sockpuppets vs many people | One reviewer can still be genuine | No |
| n_categories, category | Distinct / min category | category | | Keep-first category | No |
| avg_rating, rating_std, min/max/range | Rating stats; std NULL if 1 review | overall | Polarized products | | No |
| five_star_ratio … one_star_ratio | Share of each star | overall | Review bombing / stuffing | | No |
| rating_entropy | Same formula as reviewer | histogram | | | No |
| avg_helpful_votes | Mean helpful_votes | helpful_votes | | | No |
| review_text_coverage | Share with text | text_missing | | | No |
| first/last_review_unix, review_span_days | Product lifetime in dump | unix | | | No |
| review_velocity | review_count / (span_days+1) | derived | Sudden surge | Not attack GT | No |
| avg_reviewer_review_count | Mean of each reviewer’s review_count | reviewers.review_count | Products reviewed only by hyper-reviewers | Averages skip nothing for counts | No |
| avg_reviewer_product_diversity | Mean unique_products of reviewers | reviewers | | | No |
| avg_reviewer_rating_variability | Mean of reviewer rating_std | reviewers.rating_std | **SQL AVG skips NULL** (single-review reviewers omitted) | Underweights n=1 reviewers | No |

---

## Candidate-label safety (Track 2, not in these tables)

- Name to use: **heuristic candidate suspicious-review label**
- Not: “fake review ground truth”
- File: `dataset/cleaned/candidate_label/`
- Not joined into Track 1 feature tables in this phase
- Requires validation; expect false positives/negatives
- Do not use BFR as model inputs if predicting this label (circularity risk)
