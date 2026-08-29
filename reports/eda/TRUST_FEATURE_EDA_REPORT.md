# Trust Feature EDA Report

**Phase:** Exploratory analysis only. No models trained. No labels invented.

## 1. Executive summary

Track 1 feature tables cover **21,897,475 reviews**, **8,789,960 reviewers**, and **2,713,418 products**. Ratings are 5-star heavy (share of 5-star reviews = 0.5924). **5,168,404 reviewers (58.80%) have exactly one review**; their `rating_std` is NULL by definition and was not filled. Track 2 heuristic candidate suspicious-review label has 30,983 positives out of 1,520,951 (2.037%). It is **not** verified fraud ground truth and was **not** merged into Track 1 tables.

`review_hour` is excluded: every Unix timestamp is midnight UTC; DuckDB `hour()` in IST is 5 for all rows.

## 2. Dataset overview

| Table | Rows | Columns | Source parquet |
|---|---:|---:|---|
| review_features | 21,897,475 | 24 | `dataset/cleaned/features/review_features.parquet` |
| reviewer_features | 8,789,960 | 32 | `dataset/cleaned/features/reviewer_features.parquet` |
| product_features | 2,713,418 | 25 | `dataset/cleaned/features/product_features.parquet` |
| Track 2 analysis (separate) | 1,520,951 | — | `candidate_label/reviews_analysis.parquet` |

## 3. Review-level analysis

### Rating distribution

- rating 1: 1,842,418 (8.41%)
- rating 2: 1,130,567 (5.16%)
- rating 3: 1,701,479 (7.77%)
- rating 4: 4,251,096 (19.41%)
- rating 5: 12,971,915 (59.24%)

### Numeric summaries (selected)

| Feature | n_non_null | mean | median | std | min | max | P1 | P50 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rating | 21,897,475 | 4.159 | 5 | 1.268 | 1 | 5 | 1 | 5 | 5 |
| review_length_chars | 21,897,475 | 374.8 | 220 | 500 | 0 | 3.27e+04 | 52 | 220 | 2377 |
| review_length_words | 21,897,475 | 70.01 | 42 | 90.72 | 0 | 6141 | 10 | 42 | 436 |
| summary_length | 21,897,475 | 21.76 | 18 | 14.42 | 0 | 476 | 4 | 18 | 72 |
| helpful_votes | 21,897,475 | 1.68 | 0 | 23.58 | 0 | 5.218e+04 | 0 | 0 | 23.24 |
| product_rating_deviation | 21,897,475 | 9.722e-18 | 0.2727 | 1.099 | -3.938 | 3.961 | -3.308 | 0.2727 | 1.938 |
| reviewer_rating_deviation | 21,897,475 | 2.901e-16 | 0 | 0.8354 | -3.976 | 3.84 | -2.897 | 0 | 2 |
| exact_text_copy_count | 21,897,475 | 1.433 | 1 | 16.01 | 1 | 1004 | 1 | 1 | 1 |

### Length and helpfulness by rating

| rating | n | mean length | median length | mean helpful_votes |
|---:|---:|---:|---:|---:|
| 1 | 1,842,418 | 415.8 | 274.0 | 2.706 |
| 2 | 1,130,567 | 445.9 | 285.0 | 1.930 |
| 3 | 1,701,479 | 429.5 | 260.0 | 1.907 |
| 4 | 4,251,096 | 427.1 | 246.0 | 1.649 |
| 5 | 12,971,915 | 338.4 | 198.0 | 1.493 |

Text available on 99.9999% of reviews; summary available on 99.9986%. Exact-text copies (`is_exact_duplicate_text`): 65,535 reviews (0.30%). Hash collisions are possible; this is not a fraud label.

Year range (meaningful date feature): 1998–2014. `review_hour` unused.

## 4. Reviewer-level analysis

- Single-review reviewers: **5,168,404** (58.80%). `rating_std` NULL count = 5,168,404, of which n=1: 5,168,404, n>1: 0.
- Max review_count: **1658** (prolific ≠ fake).

| Feature | n_non_null | mean | median | P95 | P99 | max |
|---|---:|---:|---:|---:|---:|---:|
| review_count | 8,789,960 | 2.491 | 1 | 8 | 18 | 1658 |
| unique_products | 8,789,960 | 2.491 | 1 | 8 | 18 | 1658 |
| avg_rating | 8,789,960 | 4.035 | 4.5 | 5 | 5 | 5 |
| review_span_days | 8,789,960 | 219.4 | 0 | 1400 | 2582 | 5486 |
| reviews_per_active_day | 8,789,960 | 0.872 | 1 | 2 | 3.996 | 86 |
| five_star_ratio | 8,789,960 | 0.5679 | 0.6667 | 1 | 1 | 1 |
| one_star_ratio | 8,789,960 | 0.1145 | 0 | 1 | 1 | 1 |
| extreme_rating_ratio | 8,789,960 | 0.6823 | 1 | 1 | 1 | 1 |
| rating_entropy | 8,789,960 | 0.2165 | -0 | 1.041 | 1.322 | 1.609 |

### Highest-activity reviewers (EDA only, not fraud labels)

| review_count | unique_products | avg_rating | span_days | five_star_ratio |
|---:|---:|---:|---:|---:|
| 1658 | 1658 | 4.995 | 739.0 | 0.995 |
| 1159 | 1159 | 4.464 | 4061.0 | 0.542 |
| 801 | 801 | 4.317 | 3175.0 | 0.474 |
| 753 | 753 | 4.242 | 3931.0 | 0.388 |
| 742 | 742 | 4.282 | 4915.0 | 0.524 |
| 691 | 691 | 4.611 | 3818.0 | 0.745 |
| 633 | 633 | 4.581 | 2860.0 | 0.701 |
| 605 | 605 | 3.833 | 5173.0 | 0.498 |
| 587 | 587 | 4.169 | 2971.0 | 0.392 |
| 584 | 584 | 4.897 | 4308.0 | 0.925 |

## 5. Product-level analysis

- Products with one review: **1,314,695** (48.45%). Max review_count: **17846**.

| Feature | mean | median | P95 | P99 | max |
|---|---:|---:|---:|---:|---:|
| review_count | 8.07 | 2 | 26 | 113 | 1.785e+04 |
| unique_reviewers | 8.07 | 2 | 26 | 113 | 1.785e+04 |
| avg_rating | 4.138 | 4.5 | 5 | 5 | 5 |
| five_star_ratio | 0.5865 | 0.6667 | 1 | 1 | 1 |
| one_star_ratio | 0.08401 | 0 | 0.5614 | 1 | 1 |
| rating_entropy | 0.345 | -0 | 1.255 | 1.477 | 1.609 |
| review_velocity | 0.5071 | 0.2222 | 1 | 1 | 36.5 |

Products with ≥10 reviews and five_star_ratio ≥ 0.95 (rating-concentrated, **not** fraud): 3,741.
Products with review_count > P99 (high-activity): 26,349.

## 6. Category analysis

| category | reviews | mean rating | median length | mean helpful |
|---|---:|---:|---:|---:|
| Electronics | 7,559,432 | 4.052 | 255 | 2.059 |
| Clothing_Shoes_and_Jewelry | 5,504,314 | 4.192 | 185 | 0.905 |
| Home_and_Kitchen | 3,987,284 | 4.185 | 229 | 2.185 |
| Sports_and_Outdoors | 2,712,507 | 4.272 | 219 | 1.584 |
| Toys_and_Games | 1,991,993 | 4.304 | 212 | 1.495 |
| Cell_Phones_and_Accessories | 141,945 | 3.690 | 255 | 1.792 |

Category volume and rating mix differ. That is expected Amazon-category structure, not evidence of fraud.

## 7. Correlation analysis

Pearson correlations were computed in DuckDB (`corr`; pairwise nulls skipped). Percentiles on large tables use DuckDB `approx_quantile`. Spearman heatmaps use a **250,000-row random sample**.

### Reviewer Pearson |r| ≥ 0.80

- review_count vs unique_products: 1.000
- unique_products vs review_text_count: 1.000
- review_count vs review_text_count: 1.000
- avg_review_length vs median_review_length: 0.985
- rating_std vs rating_range: 0.921
- rating_range vs rating_entropy: 0.888
- unique_products vs products_per_category: 0.812
- review_count vs products_per_category: 0.812
- review_text_count vs products_per_category: 0.812
- avg_rating vs five_star_ratio: 0.807
- avg_rating vs one_star_ratio: -0.805

### Product Pearson |r| ≥ 0.80

- review_count vs unique_reviewers: 1.000
- avg_reviewer_review_count vs avg_reviewer_product_diversity: 1.000
- rating_range vs rating_entropy: 0.911
- rating_std vs rating_range: 0.866
- avg_rating vs five_star_ratio: 0.816

### Review Pearson |r| ≥ 0.80

- review_length_chars vs review_length_words: 0.997
- helpful_votes vs helpful_total: 0.995
- rating vs product_rating_deviation: 0.866

## 8. Outlier analysis

Descriptive EDA categories only — **not fraud labels**.

- HIGH_ACTIVITY reviewer: `review_count` > P95 (8) → 371,893
- EXTREME_ACTIVITY reviewer: `review_count` > P99 (18) → 86,026
- UNUSUAL_RATING_PATTERN reviewer: `review_count` ≥ 5 and `extreme_rating_ratio` ≥ 0.95 → 152,772
- UNUSUAL_REVIEW_LENGTH review: `review_length_chars` < P1 or > P99 among all reviews → 435,469
- HIGH_ACTIVITY product: `review_count` > P99 → 26,349
- Rating-concentrated product: `review_count` ≥ 10 and `five_star_ratio` ≥ 0.95 → 3,741

IQR fences (P25−1.5·IQR, P75+1.5·IQR) for reviewer `review_count` are also stored in `eda_summary.json`. Activity is heavy-tailed; IQR flags a large share of reviewers with more than a few reviews. Percentile flags are more interpretable here.

## 9. Candidate-label analysis (Track 2 only)

This is a **heuristic candidate suspicious-review label**, not verified fake-review ground truth. False positives and false negatives are expected. It lives in `separate.csv` / `candidate_label/` and was not joined onto the 21.9M Track 1 feature tables.

- n = 1,520,951; label=1: 30,983; label=0: 1,489,968; rate = 0.02037

| metric | label=0 mean | label=1 mean |
|---|---:|---:|
| overall | 2.902 | 3.178 |
| text_length | 380.4 | 324.1 |
| helpful_votes | 1.83 | 1.059 |
| bfr_CS | 0.3196 | 0.7497 |
| bfr_MNR | 0.3309 | 0.9903 |
| bfr_RB | 1.176 | 6.357 |
| bfr_NR | 12.21 | 30.69 |
| class_raw | 0.3669 | 0.4644 |

`bfr_*` gaps (especially CS, MNR, RB) are large relative to rating/text gaps. That is consistent with the label being **related to BehaviouralFeatureResult**, i.e. **TARGET LEAKAGE RISK** if BFR columns are used to predict this label.

## 10. Leakage risks

- **BFR vs Track 2 candidate_label:** TARGET LEAKAGE RISK. Do not use `bfr_*` as features for that label without proving independent labeling.
- **`class` / `rating_polarity` / `rating`:** `class` is a recode of stars. Predicting class from rating is tautological.
- **Deviations:** `product_rating_deviation` uses the product mean including the current review (small leakage for tiny products). Prefer leave-one-out if used in a model.
- **Reviewer aggregates on the same row:** `reviewer_rating_deviation` includes the current rating in the reviewer mean.
- **`review_hour`:** invalid as a behavior feature.

## 11. Important observations

- Amazon-typical 5-star skew at review, reviewer, and product levels.
- Reviewer and product activity are highly skewed (median review_count near 1; long tail).
- Length and helpfulness vary by star rating (see tables); this is consistent with sentiment, not proof of spam.
- A minority of reviews share exact text hashes (possible templates or collisions).

## 12. Limitations

- Data end in 2014. Day-level timestamps only.
- Combined corpus de-duplicated with keep-first category.
- Exact-text duplicates use hashes.
- Spearman heatmaps are sampled.
- No verified trust/fraud labels on Track 1.

## 13. Features recommended for later ML

Depends on the **target**. For unsupervised / reviewer-risk profiling (no Track 2 label):

- **review_length_chars / review_length_words:** Supported by source text; varies by rating; usable for NLP/length risk without being a recode of the target.
- **helpful_votes / helpful_ratio:** Observed engagement; biased by exposure but not a recode of class/label.
- **reviewer review_count, unique_products, reviews_per_active_day:** Directly computed activity/diversity; useful for unsupervised risk profiling. Not fraud GT.
- **five_star_ratio, one_star_ratio, rating_entropy, extreme_rating_ratio:** Describe rating concentration; interpretable. Handle n=1 separately.
- **product review_count, unique_reviewers, five_star_ratio, review_velocity:** Product-level volume and concentration for later product trust scoring.
- **is_exact_duplicate_text / exact_text_copy_count:** Template/copy signal; hash-limited; not a label.

## 14. Features NOT recommended (or restricted)

### B — investigate
- **product_rating_deviation, reviewer_rating_deviation:** Useful outlier signal but include the current row in the mean; consider leave-one-out before supervised use.
- **avg_reviewer_rating_variability on products:** AVG skips NULL std (single-review reviewers omitted).
- **review_year / month / day_of_week:** Calendar effects; not time-of-day. Check drift before using in a classifier.
- **Track 2 candidate_label as target:** Only possible rare-event target; unverified; split by reviewerID if used.

### C — redundant
- **rating_variance vs rating_std:** Variance is std squared (Pearson ~1 when both non-null).
- **review_length_words vs review_length_chars:** Both measure length; likely high correlation.
- **unique_reviewers vs review_count on products:** Often nearly 1:1 if few repeat reviewers per product.
- **five_star_ratio vs avg_rating:** Mechanically related on a 1–5 scale.
- **n_rating_k histogram vs ratios:** Ratios are normalized histograms.

### D — leakage-risk
- **bfr_* predicting Track 2 candidate_label:** TARGET LEAKAGE RISK: large mean gaps vs label; likely used to construct or is collinear with the heuristic.
- **overall/class as trust target:** class is 4–5 vs 1–3 stars.
- **candidate_label as a Track 1 feature:** Different corpus; would leak if used both as feature and target.

### E — invalid/unusable
- **review_hour:** Midnight UTC stored; hour=5 in IST for all rows; not genuine time-of-day.
- **verified_purchase:** Column does not exist.
- **rating_std filled with 0 for n=1:** Would fabricate certainty; leave NULL.
- **IDs (review_id, reviewerID, asin) as numeric model features:** Identifiers, not behavior.

## 15. Next-step recommendation

Define the modeling objective explicitly: (1) unsupervised/descriptive trust risk scoring on Track 1 features, and/or (2) a clearly caveated experiment on the Track 2 heuristic label **without BFR features**, split by `reviewerID`. Do not train a dashboard model that reports “trust accuracy” on `class` or on BFR-derived labels.
