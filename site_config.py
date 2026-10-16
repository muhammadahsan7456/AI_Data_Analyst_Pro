"""
Single source of truth for facts that are shown in more than one place
(marketing pages, meta tags, emails, exports, docs/FAQ). Every page should
read these values instead of hardcoding its own copy of the same fact, so
a correction only ever needs to be made once.

See docs/PERFORMANCE_MEASUREMENT.md for how TYPICAL_RESPONSE_TIME and
EXPORT_TYPICAL_TIME were measured.
"""

BRAND_NAME = "AI Data Analyst Pro"

# Measured against the real pipeline (question -> SQL generation -> SQL
# execution -> result returned) across 23 representative English and Roman
# Urdu questions on 2026-10-16. Median 0.63s, 95th percentile 1.56s.
# Rounded up for a safety margin over real-world network/load variance.
# Bare range, for embedding mid-sentence ("answers in {{ TYPICAL_RESPONSE_TIME }}").
TYPICAL_RESPONSE_TIME = "1 to 3 seconds"
# Same fact as a standalone statement, for headings/badges.
TYPICAL_RESPONSE_TIME_LABEL = "Typically 1 to 3 seconds"

# Measured against the real export pipeline (CSV/SVG ~1-2s, Excel ~5s,
# PDF/Word/PPTX ~6-8s because those formats render a chart image first).
# See docs/PERFORMANCE_MEASUREMENT.md for the raw per-format numbers.
EXPORT_TYPICAL_TIME = "a few seconds, depending on format and dataset size"
EXPORT_TYPICAL_TIME_LABEL = "Typically a few seconds, depending on format and dataset size"
