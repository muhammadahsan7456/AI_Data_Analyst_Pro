# Performance Measurement — Task 2 (Consistency & Safety Update)

Date: 2026-10-16
Method: real pipeline, real RDS database (no staging DB exists for this project —
see `docs/BASELINE.md`), real OpenRouter calls where the fast local pattern engine
didn't resolve a question. No numbers in this document are estimated or invented.

## 1. AI query pipeline (question -> SQL generation -> SQL execution -> result returned)

Measured via Flask's `test_client`, issuing real `POST /chat` requests (the exact
route and code path a logged-in user hits) against a disposable test account and a
synthetic 400-row dataset (Region/Category/Product/Sales/Quantity/OrderDate/
CustomerName), timing wall-clock from request send to full JSON response received.
This covers the complete server-side pipeline: dataset/session resolution, SQL
generation (local fast-path pattern engine or OpenRouter LLM fallback), SQL
execution against SQL Server, AI business-summary generation, and chart-spec
building — i.e. everything that must finish before the browser can render the
answer, summary, table, and chart. It does not include browser-side DOM rendering,
which is a separate, much smaller (sub-100ms) cost.

23 representative questions were run: 10 simple English questions, 6 more complex
analytical English questions, and 7 questions in Roman Urdu (simple and complex).
All 23 succeeded.

| # | Question | Wall-clock (s) | Reported execution_time_ms |
|---|---|---|---|
| 1 | Show top 10 customers by total sales | 8.695 | 635.63 |
| 2 | What is the total sales? | 1.411 | 248.27 |
| 3 | Show last 5 orders | 1.205 | 44.25 |
| 4 | Count how many rows | 1.357 | 32.56 |
| 5 | What is the average sales? | 0.409 | 45.24 |
| 6 | Show total sales by region | 0.407 | 38.65 |
| 7 | Top 5 products by quantity | 0.545 | 79.58 |
| 8 | What is the maximum sales value? | 0.361 | 41.23 |
| 9 | Show total sales by category | 0.455 | 28.73 |
| 10 | How many unique customers are there? | 0.482 | 64.78 |
| 11 | Which region has the highest average sales per order and why might that be? | 0.403 | n/a |
| 12 | Compare average order value across categories and explain the trend | 1.577 | 720.72 |
| 13 | What factors seem to correlate with higher sales in this dataset? | 0.687 | 42.67 |
| 14 | Summarize the key patterns in this dataset | 0.926 | 396.85 |
| 15 | Which products are underperforming relative to their category average? | 1.142 | 499.64 |
| 16 | Identify any seasonal trend in sales over the months | 1.225 | 429.26 |
| 17 | sab se zyada sales konsi region ki hai | 0.618 | 55.19 |
| 18 | total kitni sales hui hai | 0.382 | 17.40 |
| 19 | pichle 5 orders dikhao | 1.060 | 45.40 |
| 20 | har category ka average kitna hai | 1.177 | 440.23 |
| 21 | konsa product sab se zyada bika | 0.442 | 45.63 |
| 22 | kis region mein sab se zyada growth hui hai aur kyun | 0.415 | 38.27 |
| 23 | sales ka trend mahine ke hisab se batao | 0.632 | 19.91 |

**Stats (wall-clock, n=23):** min 0.361s, max 8.695s, mean 1.131s, **median 0.632s**,
**95th percentile 1.560s**.

The single 8.695s outlier (question 1, the first request of the run) is consistent
with a cold-start cost (first DB connection from the pool, first OpenRouter client
call) rather than steady-state behavior — every other request, including later
LLM-routed ones, completed in under 1.6s.

**Chosen `TYPICAL_RESPONSE_TIME` value: "1 to 3 seconds"** — rounded up from the
measured median (0.63s) and p95 (1.56s) for a safety margin over real-world
network/load variance, while still being honest about the slower end observed
(including the cold-start case). This is set in `site_config.py` and used
everywhere the UI previously claimed "sub-second", "0.005ms", or similar.

## 2. Export pipeline

Measured the same way, via real `GET /dataset/export/<id>/<format>` requests
against the same test dataset.

| Format | Wall-clock (s) | Output size |
|---|---|---|
| CSV | 1.247 | 24,273 bytes |
| SVG | 1.242 | 115,482 bytes |
| Excel | 4.934 | 23,502 bytes |
| PPTX | 6.366 | 240,001 bytes |
| PDF | 6.528 | 323,644 bytes |
| Word | 7.624 | 231,371 bytes |

PDF/Word/PPTX are the slowest because they each render a Matplotlib chart image
before assembling the document — the export code's own existing docstring
(`frontend/routes.py`, `get_or_generate_export_chart()`) already documented this
as "~1.5s a render on a 5,000-row dataset" before any document assembly on top,
which is consistent with what was measured here.

**Chosen `EXPORT_TYPICAL_TIME` value: "a few seconds, depending on format and
dataset size"** — this replaces every "under 100ms" / "sub-second" export claim
sitewide. A single fixed number was deliberately not used, since the real spread
(CSV ~1s vs. PDF/Word ~6-8s) is too wide for one honest number to cover; the
underlying per-format numbers above are kept here for reference if a more precise
claim is wanted later.

## 3. Other findings surfaced during measurement (not fixed under Task 2)

- The "85%+ of requests answered locally" claim had a real underlying mechanism
  (`ai/gemini.py`'s `fast_pattern_sql_generator`, a genuine zero-LLM-call pattern
  engine) but no code anywhere computes an actual 85% figure — it was an invented
  number. The claim was removed/reworded sitewide (see Task 2 commit) rather than
  replaced with a new unproven percentage.
- A customer review in the `Reviews` table (seeded via `database/connection.py`,
  ReviewID 1, "Haris Khan") claimed the product replaces "15+ hours a week" of
  manual work by doing it in "0.005 milliseconds" — fixed to "seconds" in both the
  seed script and the already-live database row (a non-destructive `UPDATE` of
  review text only, not a schema change), since it was a direct, in-scope ms-level
  fabrication, not a judgment call about the review's authenticity (Task 3's
  concern). The review's authenticity/"Verified" status itself was left untouched
  for Task 3.
- `templates/terms.html`'s Section 5 claimed a contractual-sounding
  "Uptime SLA... Performance Guarantee" with a "99.9% Uptime" figure and
  `index.html`'s stats counter shows the same "99.99%" figure — unlike the export
  and query-speed numbers, there is no monitoring/infrastructure in this codebase
  that measures or backs an uptime percentage. The "guarantee" language tied to
  *speed* in that section was removed (Task 2 scope), but the uptime percentage
  itself was left unchanged since it isn't a response-time claim and verifying or
  correcting it would need real infrastructure monitoring data this repo doesn't
  have — flagged here for owner review, not fixed.
