# Product trust score weighting (research prior)

This is **not** a fitted model. **No validation accuracy is claimed.**

The product trust score is a weighted mean of **available** component scores (each 0–100). If a live field is missing, that component is dropped and the remaining weights are renormalized.

| Component | Prior weight | Uses (live sample only) |
|---|---|---|
| review_quality | 0.20 | text coverage, mean review length |
| rating_consistency | 0.20 | rating entropy, 5-star share |
| reviewer_reliability | 0.20 | reviewer_id diversity or in-sample reviewer trust |
| suspicious_review_risk | 0.25 | 100 − mean per-review suspicion risk |
| review_diversity_activity | 0.15 | log sample size; penalty if dates show a tight burst |

Trust level: High ≥ 70, Moderate ≥ 45, else Low.

Per-review suspicion risk (0–100) adds documented points only when the signal exists: exact duplicate text, near-duplicate text, very short text, large rating deviation vs live product mean, helpful-votes outlier. **Not** “fake review detection.” Sentiment and star-rating polarity are separate fields.

Historical `review_features.parquet` / Track 2 `candidate_label` / BFR are not used.
