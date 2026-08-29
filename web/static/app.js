if (/Universal/i.test(document.title || "")) {
  window.location.replace("/?v=" + Date.now());
}

const $ = (id) => document.getElementById(id);
const charts = {};
const RESULT_IDS = [
  "identityBox", "errorBox", "trustBox", "breakdownBox", "statsBox", "sentimentBox",
  "suspicionBox", "reviewsBox", "reviewerBox", "explainBox", "historicalBox", "loadingBox",
  "detectBox",
];
const COMPONENT_LABELS = {
  review_quality: "Review Quality",
  rating_consistency: "Rating Consistency",
  reviewer_reliability: "Reviewer Reliability",
  suspicious_review_risk: "Behavioral consistency",
  review_diversity_activity: "Activity",
};

function destroyCharts() {
  Object.values(charts).forEach((c) => c.destroy());
  Object.keys(charts).forEach((k) => delete charts[k]);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function val(v) {
  return v === null || v === undefined || v === "" ? "—" : v;
}

function formatPriceDisplay(p) {
  if (p.price == null || p.price === "") return { price: "Price unavailable", currency: "currency unavailable" };
  const amount = Number(p.price);
  const shown = Number.isFinite(amount)
    ? amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : String(p.price);
  const code = String(p.currency || "").trim().toUpperCase();
  const symbols = { USD: "$", INR: "₹", GBP: "£", EUR: "€" };
  if (!code) return { price: `${shown} (currency unavailable)`, currency: "currency unavailable" };
  const sym = symbols[code] || "";
  return { price: `${sym}${shown} ${code}`.trim(), currency: code };
}

function hideResults() {
  RESULT_IDS.forEach((id) => $(id).classList.add("hidden"));
}

function dataStatus(data) {
  return data.data_status || data.status || "DATA_ACCESS_UNAVAILABLE";
}

function trustLevelLabel(level) {
  if (!level) return "—";
  if (level === "Moderate") return "Medium";
  return level;
}

function trustLevelClass(level) {
  const l = trustLevelLabel(level).toLowerCase();
  if (l === "high") return "high";
  if (l === "medium") return "medium";
  if (l === "low") return "low";
  return "medium";
}

function sourceLabel(data) {
  const status = dataStatus(data);
  if (status === "LIVE_AND_HISTORICAL") return "LIVE + HISTORICAL AVAILABLE";
  if (status === "LIVE") return "LIVE REVIEW DATA";
  if (status === "HISTORICAL") return "HISTORICAL DATASET ANALYSIS";
  if (status === "CATALOG_ONLY") return "CATALOG DATA AVAILABLE";
  if (status === "NOT_CONFIGURED") return "REVIEW DATA UNAVAILABLE";
  return "REVIEW DATA UNAVAILABLE";
}

function analysisStatusLabel(data) {
  const status = dataStatus(data);
  if (status === "LIVE") return "Live review analysis complete";
  if (status === "HISTORICAL") return "Historical dataset analysis complete";
  if (status === "LIVE_AND_HISTORICAL") return "Live and historical analyses available (scored separately)";
  if (status === "CATALOG_ONLY") return "REVIEW ANALYSIS UNAVAILABLE";
  if (data && data.user_message) return data.user_message;
  return "Review data unavailable";
}

function liveStatusLabel(status, data) {
  return analysisStatusLabel(Object.assign({ data_status: status }, data || {}));
}

function formatCount(n) {
  if (n == null || n === "") return "";
  return Number(n).toLocaleString();
}

function reviewCountNote(data) {
  const s = data.review_summary || {};
  const analyzed = s.n_analyzed ?? data.review_count;
  if (analyzed == null) return "";
  const available = s.n_historical_available;
  if (s.source_kind === "historical" || data.data_status === "HISTORICAL") {
    if (available != null && Number(available) > Number(analyzed)) {
      return `${formatCount(analyzed)} historical reviews analyzed (of ${formatCount(available)} in dataset for this product)`;
    }
    return `${formatCount(analyzed)} historical reviews analyzed`;
  }
  return `${formatCount(analyzed)} live reviews analyzed`;
}

function sentimentHeadline(counts) {
  const pos = Number(counts.positive || 0);
  const neu = Number(counts.neutral || 0);
  const neg = Number(counts.negative || 0);
  const mix = Number(counts.mixed || 0);
  const total = pos + neu + neg + mix;
  if (!total) return "";
  if (pos >= neu && pos >= neg && pos >= mix) return "Overall sentiment: predominantly positive.";
  if (neg >= pos && neg >= neu && neg >= mix) return "Overall sentiment: predominantly negative.";
  return "Overall sentiment: mixed.";
}

$("url").addEventListener("input", () => {
  $("detectBox").classList.add("hidden");
});

function impactLabel(direction) {
  if (direction === "positive") return "Increased trust";
  if (direction === "negative") return "Reduced trust";
  return "Limited effect";
}

function looksTechnical(name) {
  return /_|bfr_|std|entropy|ratio|n_high|candidate_label/i.test(String(name || ""));
}

function friendlyFeature(name) {
  const raw = String(name || "");
  if (COMPONENT_LABELS[raw]) return COMPONENT_LABELS[raw];
  if (looksTechnical(raw)) return raw.replace(/_/g, " ");
  return raw;
}

$("analyze").addEventListener("click", run);
$("url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") run();
});

async function run() {
  const url = $("url").value.trim();
  if (!url) {
    hideResults();
    destroyCharts();
    showError({
      data_status: "INVALID_URL",
      user_message: "Please paste a valid product URL starting with http:// or https://.",
    });
    return;
  }
  $("analyze").disabled = true;
  $("analyze").setAttribute("aria-busy", "true");
  hideResults();
  destroyCharts();
  $("loadingBox").classList.remove("hidden");
  const steps = [
    "Analyzing product...",
    "Detecting platform...",
    "Fetching product information...",
    "Fetching available reviews...",
    "Analyzing review behavior...",
    "Calculating trust...",
    "Preparing results...",
  ];
  let i = 0;
  $("loadingText").textContent = steps[0];
  const tick = setInterval(() => {
    i = Math.min(i + 1, steps.length - 1);
    $("loadingText").textContent = steps[i];
  }, 900);
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    render(data);
  } catch (err) {
    showError({
      data_status: "DATA_ACCESS_UNAVAILABLE",
      user_message: "Unable to retrieve live review data right now. Please try again.",
    });
  } finally {
    clearInterval(tick);
    $("loadingBox").classList.add("hidden");
    $("analyze").disabled = false;
    $("analyze").removeAttribute("aria-busy");
  }
}

function setPageHeading(data) {
  document.title = "E-Commerce Trust Analytics";
}

function render(data) {
  const status = dataStatus(data);
  setPageHeading(data);
  const scored = status === "LIVE" || status === "PARTIAL" || status === "HISTORICAL" || status === "LIVE_AND_HISTORICAL" || data.status === "PARTIAL";
  if (status === "CATALOG_ONLY" || status === "NOT_CONFIGURED" || (status === "DATA_ACCESS_UNAVAILABLE" && data.platform_label)) {
    renderIdentity(data);
    renderCatalogNotice(data);
    return;
  }
  if (!scored) {
    showError(data);
    return;
  }
  renderIdentity(data);
  renderTrust(data);
  renderBreakdown(data);
  renderStats(data);
  renderSentiment(data);
  renderSuspicion(data);
  renderReviews(data);
  renderReviewers(data);
  renderExplain(data);
  renderHistorical(data);
}

function renderIdentity(data) {
  const p = data.product || {};
  const status = dataStatus(data);
  const src = sourceLabel(data);
  const ok = status === "LIVE" || status === "HISTORICAL" || status === "LIVE_AND_HISTORICAL" || status === "PARTIAL";
  const catalog = status === "CATALOG_ONLY";
  $("identityBox").classList.remove("hidden");
  const priced = formatPriceDisplay(p);
  const reviewN = p.review_count != null ? p.review_count : (p.rating_count != null ? p.rating_count : null);
  $("identityBox").innerHTML = `
      <h2>Product overview</h2>
      <div class="identity">
      <div><span>PRODUCT</span><strong>${esc(val(p.title))}</strong></div>
      <div><span>BRAND</span><strong>${esc(val(p.brand))}</strong></div>
      <div><span>PLATFORM</span><strong>${esc(data.platform_label || "—")}</strong></div>
      <div><span>CATEGORY</span><strong>${esc(val(p.category))}</strong></div>
      <div><span>PRICE</span><strong>${esc(priced.price)}</strong></div>
      <div><span>CURRENCY</span><strong>${esc(priced.currency)}</strong></div>
      <div><span>RATING</span><strong>${esc(p.rating != null ? p.rating : "—")}</strong></div>
      <div><span>REVIEW COUNT</span><strong>${esc(reviewN != null ? reviewN : "—")}</strong></div>
      ${(data.catalog_product_id || p.product_id) ? `<div><span>${data.platform === "amazon" ? "ASIN" : "PRODUCT ID"}</span><strong>${esc(data.catalog_product_id || p.product_id)}</strong></div>` : ""}
      <div><span>DATA SOURCE</span><strong class="${ok ? "pos" : catalog ? "" : "neg"}">${esc(src)}</strong></div>
      <div><span>DATA STATUS</span><strong class="${ok ? "pos" : "neg"}">${esc(ok ? "REVIEW ANALYSIS AVAILABLE" : "REVIEW ANALYSIS UNAVAILABLE")}</strong></div>
    </div>
  `;
}

function renderCatalogNotice(data) {
  $("errorBox").classList.remove("hidden");
  $("errorBox").className = "card notice";
  $("errorBox").innerHTML = `
    <h2>CATALOG DATA AVAILABLE</h2>
    <p>REVIEW ANALYSIS UNAVAILABLE</p>
    <p>${esc(data.user_message || "Review-level trust analysis is unavailable because review text is not available from the current data source.")}</p>
  `;
}

function showError(data) {
  const status = dataStatus(data);
  setPageHeading(data);
  if (data.platform_label) renderIdentity(data);
  $("errorBox").className = "card error";
  $("errorBox").classList.remove("hidden");
  let title = "This URL cannot be analyzed";
  let extra = data.user_message || liveStatusLabel(status, data);
  let hint = data.required_action || "Paste a product page URL from a supported store.";
  if (status === "UNSUPPORTED_PLATFORM" || status === "INVALID_URL") {
    title = "This URL cannot be analyzed";
  }
  $("errorBox").innerHTML = `
    <h2>${esc(title)}</h2>
    <p>${esc(extra)}</p>
    <p class="hint">${esc(hint)}</p>
  `;
}

function renderTrust(data) {
  const score = data.product_trust_score;
  if (score == null) return;
  const level = trustLevelLabel(data.trust_level);
  const n = data.review_count;
  const first = (data.explanations || []).find((e) => e.interpretation);
  $("trustBox").classList.remove("hidden");
  $("trustBox").innerHTML = `
    <div class="card trust-hero">
      <div class="score-block">
        <span class="hint">Trust score</span>
        <div class="score">${esc(score)}<span> / 100</span></div>
        <div class="trust-level ${trustLevelClass(level)}">Trust Level: ${esc(level)}</div>
        <p class="hint">${esc(reviewCountNote(data) || `${n ?? "—"} reviews analyzed`)}</p>
      </div>
      <div>
        <h2>What this score is</h2>
        <p>${esc(first ? first.interpretation : "This score uses only live reviews returned for this product URL.")}</p>
        <div class="pill-row">
          <span class="pill">Trust — review trustworthiness</span>
          <span class="pill">Sentiment — tone of review text</span>
          <span class="pill">Risk score — 0 is low risk, 100 is high risk</span>
        </div>
      </div>
    </div>
  `;
}

function renderBreakdown(data) {
  const parts = (data.trust_breakdown || []).filter((c) => c.available && c.score != null);
  if (!parts.length) return;
  $("breakdownBox").classList.remove("hidden");
  $("breakdownBox").innerHTML = `
    <div class="card">
      <h2>Trust breakdown</h2>
      <p class="hint">These components contribute to Product Trust Score only. Sentiment and review risk score are shown in their own sections. Behavioral consistency is higher when fewer risk indicators fire — 100 does not mean 100% risk.</p>
      <div class="breakdown">
        ${parts.map((c) => `
          <div class="comp">
            <span>${esc(COMPONENT_LABELS[c.name] || c.name)}</span>
            <b>${esc(Math.round(c.score))}</b>
            <p class="hint">${esc(c.note || "")}</p>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function renderSentiment(data) {
  const s = data.sentiment_summary;
  if (!s || !s.n_scored) return;
  const counts = s.counts || {};
  $("sentimentBox").classList.remove("hidden");
  $("sentimentBox").innerHTML = `
    <div class="card">
      <h2>Sentiment analysis</h2>
      <p class="legend">
        <span>Positive / Neutral / Negative</span>
        <span>Not the product trust score</span>
        <span>Not the review risk score</span>
      </p>
      <p class="hint">${esc(sentimentHeadline(counts))}${s.n_unscored_missing_text ? ` ${s.n_unscored_missing_text} review(s) had no text and were not scored.` : ""}</p>
      <div class="charts"><div class="chart-card"><canvas id="cSent"></canvas></div></div>
    </div>
  `;
  const sentOrder = [
    ["positive", "Positive", "#3ee0b2"],
    ["neutral", "Neutral", "#f3c14e"],
    ["negative", "Negative", "#ff6b7a"],
    ["mixed", "Mixed", "#6aa8ff"],
  ];
  const sentLabels = sentOrder.filter(([k]) => k !== "mixed" || Number(counts[k] || 0) > 0);
  charts.sent = new Chart($("cSent"), {
    type: "doughnut",
    data: {
      labels: sentLabels.map((x) => x[1]),
      datasets: [{ data: sentLabels.map((x) => Number(counts[x[0]] || 0)), backgroundColor: sentLabels.map((x) => x[2]) }],
    },
    options: { plugins: { title: { display: true, text: "Positive / Neutral / Negative", color: "#e8eef8" } } },
  });
}

function renderSuspicion(data) {
  const s = data.suspicious_review_summary;
  if (!s) return;
  const n = data.review_count || 0;
  const flagged = (s.n_high || 0) + (s.n_medium || 0);
  const pct = n ? Math.round((flagged / n) * 100) : 0;
  const reasons = {};
  (data.review_analyses || []).forEach((r) => {
    (r.reasons || []).forEach((x) => { reasons[x] = (reasons[x] || 0) + 1; });
  });
  const top = Object.entries(reasons).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const riskScore = s.mean_suspicion_risk;
  $("suspicionBox").classList.remove("hidden");
  $("suspicionBox").innerHTML = `
    <div class="card">
      <h2>Review risk score</h2>
      <p class="hint">0 is low risk and 100 is high risk. This is a behavioral indicator, not a claim that reviews are fake.</p>
      <div class="grid">
        <div class="metric"><span>Risk score</span><b>${riskScore == null ? "—" : `${esc(riskScore)} / 100`}</b></div>
        <div class="metric"><span>Risk level</span><b>${esc(s.level || "—")}</b></div>
        <div class="metric"><span>Reviews with risk indicators</span><b>${flagged} (${pct}%)</b></div>
      </div>
      ${top.length ? `<h3>Major detected risk signals</h3><ul class="fields">${top.map(([k, v]) => `<li>${esc(k)} — ${v}</li>`).join("")}</ul>` : "<p class=\"hint\">No potentially unusual risk indicators fired on this live sample.</p>"}
      <div class="charts"><div class="chart-card"><canvas id="cRisk"></canvas></div></div>
    </div>
  `;
  charts.risk = new Chart($("cRisk"), {
    type: "doughnut",
    data: {
      labels: ["Low", "Medium", "High"],
      datasets: [{ data: [s.n_low || 0, s.n_medium || 0, s.n_high || 0], backgroundColor: ["#3ee0b2", "#f3c14e", "#ff6b7a"] }],
    },
    options: { plugins: { title: { display: true, text: "Risk level of analyzed reviews", color: "#e8eef8" } } },
  });
}

function renderReviews(data) {
  const reviews = data.reviews || [];
  const analyses = data.review_analyses || [];
  if (!reviews.length && !analyses.length) return;
  const byId = {};
  reviews.forEach((r) => { if (r.review_id) byId[r.review_id] = r; });
  $("reviewsBox").classList.remove("hidden");
  const cards = (analyses.length ? analyses : reviews.map((r) => ({ review_id: r.review_id, rating: r.rating }))).map((a, i) => {
    const raw = byId[a.review_id] || reviews[i] || {};
    const text = raw.text || "";
    const rating = a.rating ?? raw.rating;
    const sentiment = a.sentiment_label;
    const risk = a.suspicion_risk;
    const bits = [];
    if (raw.title) bits.push(`<span>Title: ${esc(raw.title)}</span>`);
    if (rating != null && rating !== "") bits.push(`<span>Star rating: <strong>${esc(rating)}</strong></span>`);
    if (sentiment) bits.push(`<span>Sentiment: ${esc(sentiment)}</span>`);
    if (risk != null && risk !== "") bits.push(`<span>Risk score: <strong>${esc(risk)} / 100</strong>${a.risk_level ? ` (${esc(a.risk_level)})` : ""}</span>`);
    if (raw.reviewer_name) bits.push(`<span>Reviewer: ${esc(raw.reviewer_name)}</span>`);
    if (raw.helpful_votes != null && raw.helpful_votes !== "") bits.push(`<span>Helpful votes: ${esc(raw.helpful_votes)}</span>`);
    if (raw.review_date) bits.push(`<span>Date: ${esc(String(raw.review_date).slice(0, 10))}</span>`);
    return `
      <article class="review-card">
        <div class="review-meta">${bits.join("") || "<span>Limited fields on this review</span>"}</div>
        ${text ? `<p class="review-text">${esc(text)}</p>` : "<p class=\"hint\">Review text was not available for this item.</p>"}
        ${(a.reasons || []).length ? `<p class="hint">Risk indicators: ${a.reasons.map(esc).join("; ")}</p>` : ""}
      </article>
    `;
  }).join("");
  $("reviewsBox").innerHTML = `<div class="card"><h2>Review analysis</h2><p class="hint">${esc(reviewCountNote(data) || "Reviews analyzed")}. ${(data.review_summary || {}).source_kind === "historical" ? "These reviews are from the project's historical dataset, not a live storefront feed." : "This is the live sample for this URL, not the product’s full review history."}</p>${cards}</div>`;
}

function renderReviewers(data) {
  const s = data.reviewer_summary || {};
  const rows = (data.reviewer_analyses || []).filter((r) => r && r.reviewer_id);
  if (!s.available || !rows.length) {
    return;
  }
  $("reviewerBox").classList.remove("hidden");
  $("reviewerBox").innerHTML = `
    <div class="card">
      <h2>Reviewer analysis</h2>
      <p class="hint">${(data.review_summary || {}).source_kind === "historical" ? "Based on reviewers in this historical sample." : "Based only on reviewers seen in this live sample."}</p>
      <div class="table-wrap">
      <table class="table">
        <thead><tr><th>Reviewer</th><th>Reviews</th><th>Avg rating</th><th>Trust</th><th>Risk</th><th>Signals</th></tr></thead>
        <tbody>
          ${rows.map((r) => `<tr>
            <td>${esc(r.reviewer_id)}</td>
            <td>${esc(r.review_count)}</td>
            <td>${esc(val(r.avg_rating))}</td>
            <td>${r.reviewer_trust_score == null ? "—" : r.reviewer_trust_score}</td>
            <td>${esc(r.risk_level)}</td>
            <td>${esc((r.reasons || []).join("; ") || "—")}</td>
          </tr>`).join("")}
        </tbody>
      </table>
      </div>
    </div>
  `;
}

function renderExplain(data) {
  const items = (data.explanations || []).filter((e) => e && e.interpretation);
  if (!items.length) return;
  $("explainBox").classList.remove("hidden");
  $("explainBox").innerHTML = `
    <div class="card">
      <h2>Why this score?</h2>
      <div class="table-wrap">
      <table class="table">
        <thead><tr><th>Signal</th><th>Observed</th><th>What it means</th><th>Impact</th></tr></thead>
        <tbody>
        ${items.map((e) => `<tr>
            <td>${esc(friendlyFeature(e.feature))}</td>
            <td>${esc(val(e.observed_value))}</td>
            <td>${esc(e.interpretation)}</td>
            <td>${esc(impactLabel(e.direction))}</td>
          </tr>`).join("")}
        </tbody>
      </table>
      </div>
    </div>
  `;
}

function renderStats(data) {
  const s = data.review_summary || {};
  const ps = s.product_stats || {};
  const rs = s.review_feature_stats || {};
  const dist = s.rating_distribution || {};
  const items = [];
  const analyzed = s.n_analyzed ?? data.review_count;
  const available = ps.review_count || s.n_historical_available;
  if (analyzed != null) items.push(["Reviews analyzed", formatCount(analyzed)]);
  if (available != null && Number(available) !== Number(analyzed)) {
    items.push(["Historical reviews for this product", formatCount(available)]);
  }
  if (ps.unique_reviewers != null) items.push(["Unique reviewers", formatCount(ps.unique_reviewers)]);
  const avg = s.avg_rating != null ? s.avg_rating : ps.avg_rating;
  if (avg != null) items.push(["Average rating", Number(avg).toFixed(2)]);
  if (rs.median_rating != null) items.push(["Median rating", Number(rs.median_rating).toFixed(2)]);
  if (rs.avg_helpful_votes != null) items.push(["Avg helpful votes", Number(rs.avg_helpful_votes).toFixed(2)]);
  const stars = ["5", "4", "3", "2", "1"].filter((k) => dist[k] != null).map((k) => `${k}★ ${dist[k]}`);
  if (stars.length) items.push(["Rating distribution", stars.join(" · ")]);
  if (ps.five_star_ratio != null) items.push(["Five-star share", `${Math.round(ps.five_star_ratio * 100)}%`]);
  if (!items.length) return;
  $("statsBox").classList.remove("hidden");
  $("statsBox").innerHTML = `
    <div class="card">
      <h2>Review summary</h2>
      <p class="hint">${s.source_kind === "historical" ? "Statistics for this exact product identifier in the historical dataset." : "Statistics for the reviews analyzed for this URL."}</p>
      <div class="grid">
        ${items.map(([k, v]) => `<div class="metric"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}
      </div>
    </div>
  `;
}

function renderHistorical(data) {
  const h = data.historical_analysis;
  if (!h || dataStatus(data) !== "LIVE_AND_HISTORICAL") return;
  const n = h.n_sampled;
  const available = h.n_available;
  const liveN = data.review_count;
  const liveScore = data.product_trust_score;
  $("historicalBox").classList.remove("hidden");
  $("historicalBox").innerHTML = `
    <div class="card">
      <h2>HISTORICAL DATASET ANALYSIS</h2>
      <p class="hint">${esc(h.disclaimer || "These reviews are from the project's historical dataset, not a live storefront feed.")}</p>
      <p class="hint">Live and historical trust scores are from different sources. These scores are calculated independently and are not merged.</p>
      <div class="grid">
        <div class="metric"><span>LIVE REVIEW DATA</span><b>${liveScore == null ? "—" : `${liveScore} / 100`}</b><p class="hint">${esc(formatCount(liveN) || "—")} live reviews</p></div>
        <div class="metric"><span>HISTORICAL DATASET ANALYSIS</span><b>${h.product_trust_score == null ? "—" : `${h.product_trust_score} / 100`}</b><p class="hint">${esc(formatCount(available || n))} historical reviews · Trust level: ${esc(trustLevelLabel(h.trust_level))}</p></div>
      </div>
    </div>
  `;
}
