# Data Quality Report — Trust Analytics System

**Stage:** audit only. No cleaning was applied. Original files in `dataset/csv_files/` were not modified.

**Method:** chunked Pandas reads (`chunksize=200,000`), string dtypes, incremental missing/duplicate/label statistics. Duplicate and overlap counts use 64-bit hashes of keys (collisions possible but rare). `BehaviouralFeatureResult` was fully parsed for all of `part.csv` and for the first 200,000 rows of `separate.csv`.

**Generated:** 2026-08-17

---

## 1. Dataset overview

Eight Amazon-style product-review CSVs (12.17 GB). Combined parsed rows **across files, not de-duplicated:** **23,843,496**.

There are two families of files:

1. **Category dumps** (6 files): standard review fields plus `class`. No `label`, no `BehaviouralFeatureResult`.
2. **Derived/auxiliary files** (`part.csv`, `separate.csv`): add `BehaviouralFeatureResult` and `label`. Different schemas from each other and from the category dumps.

No data dictionary exists in the repository README. Meanings below are inferred from **observed values and co-occurrence**, not from official documentation.

---

## 2–4. Files analyzed, sizes, row/column counts

| File | Size | Parsed rows | Newlines | Line−row delta | Cols |
|---|---:|---:|---:|---:|---:|
| `Cell_Phones_and_Accessories.csv` | 81.87 MB | 141,945 | 141,948 | 2 | 12 |
| `Clothing_Shoes_and_Jewelry.csv` | 2.27 GB | 5,504,331 | 5,504,384 | 52 | 12 |
| `Electronics.csv` | 4.37 GB | 7,574,169 | 7,574,297 | 127 | 12 |
| `Home_and_Kitchen.csv` | 1.98 GB | 3,988,482 | 3,988,553 | 70 | 12 |
| `Sports_and_Outdoors.csv` | 1.46 GB | 3,013,256 | 3,013,280 | 23 | 12 |
| `Toys_and_Games.csv` | 959.58 MB | 1,997,140 | 1,997,178 | 37 | 12 |
| `part.csv` | 67.10 MB | 99,939 | 99,940 | 0 | 13 |
| `separate.csv` | 1.00 GB | 1,524,234 | 1,524,253 | 18 | 13 |

Small positive line−row deltas are consistent with a few quoted fields that contain embedded newlines, not with massively malformed CSVs. Parser row counts are trustworthy.

**Category-file schema (12 columns):**  
`_id`, `reviewerID`, `asin`, `reviewerName`, `helpful`, `reviewText`, `overall`, `summary`, `unixReviewTime`, `reviewTime`, `category`, `class`

**`part.csv` schema (13 columns):**  
same review fields including `_id`, plus `BehaviouralFeatureResult`, `label`. **No `class`.**

**`separate.csv` schema (13 columns):**  
same review fields **without `_id`**, plus `class`, `BehaviouralFeatureResult`, `label`.

---

## 5. Missing-value analysis

Fully empty rows (all fields blank/NaN-like): **0 in every file**.

Identifiers `reviewerID`, `asin`, `helpful`, `overall`, `unixReviewTime`, `reviewTime`, `category` are **complete** in every file.

| File | `reviewerName` missing | `reviewText` missing | `summary` missing | `class` missing | `label` missing |
|---|---:|---:|---:|---:|---:|
| Cell_Phones… | 951 (0.67%) | 0 | 9 | 0 | n/a |
| Clothing… | 13,856 (0.25%) | 10 | 72 | 0 | n/a |
| Electronics | 96,883 (1.28%) | 3 | 136 | 0 | n/a |
| Home_and_Kitchen | 31,838 (0.80%) | 4 | 44 | 0 | n/a |
| Sports_and_Outdoors | 13,174 (0.44%) | 3 | 23 | 0 | n/a |
| Toys_and_Games | 11,057 (0.55%) | 0 | 24 | 0 | n/a |
| part.csv | 734 (0.73%) | 0 | 6 | n/a | **80,190 (80.24%)** |
| separate.csv | 10,371 (0.68%) | 1 | 43 | **1,324,239 (86.88%)** | 0 |

`BehaviouralFeatureResult` is never missing in `part.csv` or `separate.csv`.

**Do not fill missing `label` or `class` with 0.** In `part.csv`, 80% of `label` is missing and every *present* `label` is `0.0`; converting NaN→0 would fabricate a complete negative class. In `separate.csv`, missing `class` is the majority state, not a coding of the negative class.

---

## 6. Duplicate analysis

Within each **category dump**, hashed `_id`, `(reviewerID, asin, unixReviewTime)`, and `(reviewerID, asin, reviewText)` show **zero extra hits**. Rows are unique inside those files.

| File | Unique `reviewerID` | Unique `asin` | Extra triple hits | Extra same-text hits |
|---|---:|---:|---:|---:|
| Cell_Phones… | 129,464 | 13,788 | 0 | 0 |
| Clothing… | 3,023,121 | 1,108,410 | 0 | 0 |
| Electronics | 4,099,875 | 469,616 | 0 | 0 |
| Home_and_Kitchen | 2,386,951 | 398,219 | 0 | 0 |
| Sports_and_Outdoors | 1,864,348 | 459,654 | 0 | 0 |
| Toys_and_Games | 1,215,502 | 310,714 | 0 | 0 |
| part.csv | 93,792 | 74,806 | 39 | 39 |
| separate.csv | 1,282,618 | 610,937 | 3,283 | 3,283 |

`part.csv`: 99,939 rows → 99,900 unique triples (39 extras). `_id` still unique (99,939), so extras are duplicate review keys with distinct Mongo ids.

`separate.csv`: 1,524,234 rows → 1,520,951 unique triples (3,283 extras). No `_id` column.

---

## 7. Data-type analysis

Observed types after string read + coercion:

| Column | Observed type | Notes |
|---|---|---|
| `_id` | string | 100% Mongo-style `{'$oid': ...}` in category files and `part.csv` |
| `reviewerID`, `asin` | string | complete; `asin` is not always a 10-character Amazon ASIN (short numeric ids appear) |
| `reviewerName` | string | missing/generic names occur; not a stable ID |
| `helpful` | serialized `[votes, total]` | **100% parseable** in all files; almost no `votes > total` (0–1 rows) |
| `reviewText`, `summary` | string | UTF-8; **0** replacement-character (`U+FFFD`) rows |
| `overall` | integer-valued 1–5 | **0** values outside 1–5; **0** fractional ratings |
| `unixReviewTime` | Unix seconds | range **1998-08-03 to 2014-07-23 UTC** (Amazon review era) |
| `reviewTime` | date string | **0** unparseable; **0** date mismatches vs `unixReviewTime` |
| `category` | string | one value per category dump; mixed in `part`/`separate` |
| `class` | 0.0 / 1.0 | complete in category dumps; mostly missing in `separate.csv` |
| `label` | 0 / 1 (or 0.0) | only in `part`/`separate` |
| `BehaviouralFeatureResult` | dict of 13 floats | derived metrics (see §8) |

`helpful` means (votes, total votes): category files ~0.9–2.2 helpful votes; totals slightly higher. Not a trust label.

---

## 8–9. Label analysis and class distribution

### A. What `class` represents

In **every category dump**, `class` is a **perfect recode of `overall`:**

- `overall` ∈ {4, 5} → `class` = 1.0
- `overall` ∈ {1, 2, 3} → `class` = 0.0

Zero exceptions in 22.2 million category-file rows.

In `separate.csv`, whenever `class` is **present**, the same rule holds. `class` is missing on 1,324,239 rows (86.88%), spread across all star ratings.

This is **star-rating polarity / coarse sentiment**, not reviewer trust, not spam, not review quality.

| File | class=1 (4–5★) | class=0 (1–3★) | Missing class |
|---|---:|---:|---:|
| Cell_Phones… | 93,275 (65.7%) | 48,670 | 0 |
| Clothing… | 4,334,882 (78.8%) | 1,169,449 | 0 |
| Electronics | 5,751,275 (75.9%) | 1,822,894 | 0 |
| Home_and_Kitchen | 3,165,285 (79.4%) | 823,197 | 0 |
| Sports_and_Outdoors | 2,481,105 (82.3%) | 532,151 | 0 |
| Toys_and_Games | 1,662,754 (83.3%) | 334,386 | 0 |
| separate.csv | 73,773 | 126,222 | 1,324,239 |
| part.csv | n/a | n/a | n/a (no column) |

### B. What `label` represents

`label` is **not** `class`. Evidence:

- `part.csv` has `label` and no `class`.
- `separate.csv` cross-tab (rows):

| class \ label | label=0 | label=1 |
|---|---:|---:|
| class missing | 1,297,345 | 26,894 |
| class=0 | 124,016 | 2,206 |
| class=1 | 71,860 | 1,913 |

`label=1` occurs under missing, 0, and 1 `class` values. `label` is also **not** a threshold on `overall` (`separate.csv`):

| overall | label=0 | label=1 |
|---|---:|---:|
| 1 | 392,535 | 7,298 |
| 2 | 237,943 | 4,698 |
| 3 | 362,936 | 5,037 |
| 4 | 124,511 | 3,159 |
| 5 | 375,296 | 10,821 |

`label=1` rate is ~2.03% (31,013 / 1,524,234). That **looks like** a rare-event / spam-style flag, but **column name and imbalance do not prove ground truth.**

`part.csv` `label`: **only `0.0` or missing**. Never `1`. Present `label=0.0` on 19,749 rows (19.8%); missing on 80,190 (80.2%). Unusable alone as a binary target.

**Provenance is unknown.** There is no codebook in this repo stating who labeled `label` (human, Amazon, heuristic, or a function of BFR).

### C. What `BehaviouralFeatureResult` represents

A serialized dict with the **same 13 keys on every parsed row:**  
`CS`, `MNR`, `RB`, `RC`, `PR`, `NR`, `FR`, `RSP`, `AW`, `RD`, `RL`, `ER`, `PC`.

Ranges (from full `part.csv` parse) show they are **numeric engineered features**, not raw Amazon fields:

| Key | min | max | mean (`part.csv`) | Compatible with (hypothesis only) |
|---|---:|---:|---:|---|
| CS | 0 | ~1 | 0.32 | similarity / cosine-like score |
| MNR | 0 | 1 | 0.52 | bounded ratio (often “max reviews in a day” normalized) |
| RB | 0 | 109 | 1.46 | count (burst?) |
| RC | 0 | 1660 | 10.7 | review count |
| PR | 0 | 100 | 55.4 | percent (positive-review share?) |
| NR | 0 | 100 | 13.0 | percent (negative-review share?) |
| FR | 0 | 1 | 0.10 | ratio |
| RSP | 0 | 1 | 0.29 | indicator/ratio |
| AW | 0 | 5466 | 515 | time gap / wait (days?) |
| RD | 0 | 4 | 1.36 | rating deviation (stars) |
| RL | 2 | 14946 | 362 | **equals review-text length scale** (mean RL 362 vs mean `reviewText` length 362 in `part.csv`) |
| ER | 0 | 1 | 0.53 | binary/ratio |
| PC | 0 | 100 | 2.44 | percent |

**`RL` in `part.csv` has mean 362.19; `reviewText` character length has mean 362.19.** That is strong evidence `RL` is **derived from review text length**, not an independent Amazon field.

These columns are **derived**, not original marketplace fields. Exact formulas are **not documented in this project.** Do not invent definitions beyond what the numbers support.

### D–H. Original vs derived; reliability; missingness; scheme differences

| Question | Finding |
|---|---|
| Original Amazon-like fields? | Yes: `reviewerID`, `asin`, `reviewerName`, `helpful`, `reviewText`, `overall`, `summary`, timestamps, `category`. `_id` is a Mongo import artifact. |
| Derived? | `class` (from `overall`); `BehaviouralFeatureResult` (from reviewer/text/rating statistics); `label` **unknown** |
| Reliable trust ground truth? | **`class`: no** (it is sentiment of stars). **`label`: not demonstrated.** It is the only non-rating binary, but provenance is missing. |
| Related to trust? | `class`: sentiment. BFR: behavioral *features*, not labels. `label`: possibly suspicious/spam-style, unproven. |
| Why missing? | `label` missing in `part.csv` looks like an incomplete labeling pass. `class` missing in `separate.csv` looks like `class` was only attached to a subset (still the rating rule when present). |
| Different schemes? | Yes. Category files: `class` only. `part.csv`: `label` ∈ {0, missing} only. `separate.csv`: `label` ∈ {0,1} complete; `class` mostly missing. |

### I. Are `part.csv` and `separate.csv` derived from the category dumps?

**`part.csv` is a subset of `separate.csv`:** 99,900 / 99,900 unique triples in `part.csv` (100%) also appear in `separate.csv`. `part.csv` is ~6.57% of `separate.csv` unique triples.

They are **not** a simple labeled slice of the six category files. Overlap of `separate.csv` with category dumps is small:

| Category file ∩ `separate.csv` | Intersection triples | % of separate |
|---|---:|---:|
| Clothing… | 25,575 | 1.68% |
| Sports… | 13,020 | 0.86% |
| Electronics | 11,964 | 0.79% |
| Cell_Phones… | 427 | 0.03% |
| Toys… | 276 | 0.02% |
| Home… | 102 | 0.01% |

Roughly **~3%** of `separate.csv` triples appear in the category dumps. `separate.csv` also has a **very different rating mix** (mean `overall` **2.91**) vs category dumps (means **~3.69–4.30**, 5-star heavy). That is sampling/selection, not a random subset.

---

## 10. Dataset inconsistencies and overlap

**Schema:** `_id` vs no `_id`; `class` vs no `class`; `label`/BFR only on two files.

**Cross-category duplicate reviews:** the same `(reviewerID, asin, unixReviewTime)` can appear in two category files. Largest:

- Clothing ∩ Sports: **298,296** triples (5.42% of Clothing, 9.90% of Sports). Consistent with products listed in two Amazon categories (the sample tutu review pattern).
- Cell_Phones ∩ Electronics: **9,711** (6.84% of Cell_Phones).
- Other category pairs: hundreds to a few thousand, generally <0.2%.

Concatenating category files **without de-overlapping triples would duplicate those reviews.**

---

## 11. Potential data leakage

| Feature | Risk | Recommendation |
|---|---|---|
| `overall` if target is `class` | **Definitional leakage** (`class` is a function of `overall`) | Exclude `overall` from features **or** do not use `class` as target |
| `class` if target is `label` | Related sentiment, not identical | Do not use as a feature unless a distinct analysis needs it |
| `BehaviouralFeatureResult` if target is `label` | **High circularity risk** if `label` was produced from these metrics | **Exclude from ML inputs** until provenance proves independence. Keep for EDA. |
| `RL` | Derived from text length | If text length is already a feature, `RL` is redundant |
| Reviewer aggregates (count, mean rating, etc.) computed including the target row | Standard leakage | Compute from *other* reviews of the same reviewer, ideally earlier in time |
| `label` / `class` as inputs to a model of the same field | Direct leakage | Never |

Helpfulness is **not** leakage for a trust target (it is a separate observed signal), but it is **biased by exposure** and is not ground-truth trust.

---

## 12. Recommended cleaning strategy (not executed)

Wait for approval. Proposed, justified steps:

1. **Leave raw CSVs untouched.** Write outputs only under `dataset/cleaned/`.
2. **Do not merge `class` and `label`.** Do not NaN→0 on labels.
3. **Parse `helpful` → `helpful_votes`, `helpful_total`, `helpful_ratio`** (ratio only where total>0).
4. **Canonical timestamp = `unixReviewTime`**; keep `reviewTime` only if needed for display; they already match.
5. **Extract `_id.$oid` as `mongo_id`** for joins; do not use as an ML feature.
6. **Preserve `reviewText`.** Flag empty/very short (<5 chars) rows; do not drop by default.
7. **Category corpus:** optionally concatenate the six dumps with `source_file`, then drop duplicate triples, preferring a documented rule (e.g. keep first source). This is for EDA/behavior, not for fake `class`-as-trust modeling.
8. **`part.csv`:** treat as a subset of `separate.csv`; do not train on both as independent samples.
9. **Parse BFR into columns for analysis only**; default ML feature set should omit them.
10. **Deduplicate** `separate.csv` 3,283 extra triples after inspecting whether they are exact copies.
11. **Do not downsample or invent `label=1` rows.**

---

## 13. Final feature recommendations

**Can be used (computed from actual columns):**

- Review-level: `reviewText` (NLP), `summary`, `overall` (as a rating feature only if target ≠ `class`), parsed helpfulness, `category`, `unixReviewTime`, text length, token/repetition stats.
- Reviewer-level (from `reviewerID`, which is never missing): number of reviews, mean/std `overall`, mean text length, mean helpfulness ratio, time between reviews, distinct `asin`/`category` counts, repeated-text flags across products.

**Cannot be fabricated:** trust scores, “suspicious” flags, reviewer graphs beyond IDs present, anything not in these CSVs.

**BFR:** analysis/explanation only until formulas and `label` provenance are known.

---

## 14. Final target recommendation

**There is no demonstrated, reliable ground-truth for “reviewer/review trust” in the six category files.**

| Candidate | Valid as trust target? | Valid as something else? |
|---|---|---|
| `class` | **No.** It is 4–5★ vs 1–3★. | Yes: **sentiment / polarity classification** (trivial if `overall` is an input; nontrivial from text only). |
| `label` in `part.csv` | **No.** Only 0 or missing; no positive class. | Incomplete extract. |
| `label` in `separate.csv` | **Not proven.** Only binary that is independent of `overall`. Provenance unknown; BFR leakage risk. | Candidate **if** you accept it as an unverified rare-event label and **exclude BFR** from features. |
| No label | — | **Reviewer-behavior analytics / anomaly description** without claiming a classifier of trust. |

**Preferred honest project design:**

1. **Primary:** explainable reviewer- and review-level analytics on the category dumps (behavior features listed above), clearly *not* claiming `class` is trust.
2. **Optional supervised experiment:** `separate.csv` `label` as a *candidate* suspicious/rare class, with documented caveats, no BFR features, stratified splits by `reviewerID`.
3. **Optional separate track:** sentiment from `reviewText` → `class` or `overall`, labeled as sentiment.

Do **not** train “trust accuracy” on `class`. That would be scientifically false.

---

## 15. Reasons for excluding columns (from ML feature sets)

| Column | Exclude from ML? | Why |
|---|---|---|
| `_id` / `mongo_id` | Yes as feature | Import identifier |
| `reviewerName` | Yes as feature | Unstable, missing, often “Amazon Customer”; keep `reviewerID` |
| `reviewTime` | Yes if `unixReviewTime` kept | Redundant and already consistent |
| `class` | Yes if pretending trust; no if sentiment target | Definitionally `overall` polarity |
| `overall` | Yes if target is `class` | Leakage |
| `label` | Yes as input if it is the target | Leakage |
| `BehaviouralFeatureResult` | Default yes as ML input | Derived; likely circular with `label` |
| `reviewText` | **No** | Needed for NLP; missingness is tiny |

---

## 16. Limitations

- No official codebook for `label` or BFR keys.
- Reviews end in July 2014 (Amazon snapshot era); not current marketplace behavior.
- Helpfulness ≠ trust.
- `separate.csv` is a different, rating-rebalanced extract; results on it will not match category-dump base rates.
- Hash-based overlap can theoretically collide; 100% `part`⊂`separate` is still unambiguous given identical key cardinality.
- BFR on `separate.csv` fully parsed for 200,000 / 1,524,234 rows; keys were uniform in that sample and in all of `part.csv`.
- Cross-listed products mean “category” is not a clean partition.

---

## Audit conclusions (A–K)

**A. Files found:** eight CSVs listed above (12.17 GB, 23.84 million parsed rows summing files).

**B. Contents:** Amazon review records. Six category dumps + `part.csv` (subset of `separate.csv` with incomplete `label`) + `separate.csv` (BFR + complete `label` 0/1).

**C. Problems:** `class` is not trust; `label` provenance unknown; `part.csv` has no positive `label`; `separate.csv` `class` 87% missing; cross-file overlap (especially Clothing/Sports); 3,283 duplicate keys in `separate.csv`; missing display names; tiny number of empty review texts. Data are otherwise internally consistent (ratings, timestamps, helpful lists).

**D. `class`:** binarized star rating (4–5 vs 1–3). Sentiment, not trust.

**E. `class` vs `label`:** **different.** Do not merge. `label` is not a function of `overall`.

**F. Which dataset to use:**

- Behavior / EDA / (optional) sentiment: the **six category files**, de-overlapped if concatenated.
- The only file with both `label=0` and `label=1`: **`separate.csv`**. Use only with caveats. **`part.csv` is not an independent dataset.**

**G. Retain:** `reviewerID`, `asin`, `reviewText`, `summary`, `overall` (as rating), parsed `helpful`, `unixReviewTime`, `category`, `source_file` if concatenating. Keep `label` only as a *candidate* target on `separate.csv`. Keep BFR parsed for analysis.

**H. Remove from ML features:** `_id`, `reviewerName`, `reviewTime` (redundant), `class` as trust, BFR as default predictors of `label`.

**I. Final ML target:** **Do not use `class` as trust.** There is **no verified trust ground truth.** The only possible supervised rare-event target is `separate.csv` `label`, **unverified**. Otherwise the valid objective is **unsupervised/descriptive trust-related analytics** (behavior, repetition, helpfulness, rating patterns) plus a clearly labeled sentiment model if desired.

**J. Cleaning steps:** see §12; **not run yet.**

**K. Suitable for Trust Analytics?** **Partially.** The data **support** reviewer-behavior measurement and explainable EDA. They **do not support** a defensible claim of “trust classification accuracy” unless `label` is independently documented. Using `class` as trust would be incorrect.

---

**Next step:** wait for approval before implementing `scripts/clean_dataset.py` and writing cleaned outputs.
