# AI Data Analyst Pro — Post-Launch SEO & Off-Site Authority Checklist

**Target Domain:** `https://aidataanalystpro.co.uk`  
**Platform Architecture:** Flask + Jinja2 (SSR) on Oracle Cloud VM (`84.235.244.179`) behind Nginx  
**Target Search Markets:** Global English B2B SaaS, Pakistan & regional SME market (PKR pricing, local payment methods)

---

## 1. Webmaster Tools & Search Engine Submission

### 1.1 Google Search Console (GSC)
- [x] **Meta Verification Tag Injected:** Verification meta tag hook added to `templates/landing_base.html` and configurable via `GOOGLE_SITE_VERIFICATION` in `.env`.
- [ ] **Submit Sitemap:** In GSC sidebar, navigate to **Sitemaps** > Enter `sitemap.xml` (Full URL: `https://aidataanalystpro.co.uk/sitemap.xml`) > Click **Submit**.
- [ ] **Request URL Inspection & Favicon Refresh:**
  1. Paste `https://aidataanalystpro.co.uk/` into the top search bar in GSC.
  2. Click **Test Live URL**.
  3. Click **Request Indexing**.
  *(This triggers Google's homepage and Favicon crawler to fetch the new 512x512 / 192x192 SVG/PNG branding and purge the legacy cached icon in Google SERP snippets within 3 to 10 days).*
- [ ] **Core Web Vitals Monitoring:** Check the **Page Experience** and **Core Web Vitals** reports 14 days after launch. Ensure 0 URLs have CLS > 0.1 or LCP > 2.5s.

### 1.2 Bing Webmaster Tools
- [ ] **Sign in & Import:** Sign in at [bing.com/webmasters](https://www.bing.com/webmasters) and choose **Import from Google Search Console** (one-click sync).
- [ ] **Meta Tag Verification:** If setting up independently, set `BING_SITE_VERIFICATION=your_code_here` in `.env`.
- [ ] **IndexNow API:** Submit changed URLs via Bing IndexNow API for instant crawl scheduling.

---

## 2. Analytics & Conversion Tracking

### 2.1 Google Analytics 4 (GA4)
- [x] **GA4 Script Injected:** Template hook configured in `templates/landing_base.html` via `GA4_MEASUREMENT_ID`.
- [ ] **Configure GA4 Property:**
  1. Create a GA4 Web Data Stream for `https://aidataanalystpro.co.uk`.
  2. Copy the Measurement ID (e.g., `G-XXXXXXXXXX`).
  3. Add `GA4_MEASUREMENT_ID=G-XXXXXXXXXX` into `.env` on the VPS server.
- [ ] **Custom Event Tracking:**
  - `file_upload_attempt`: Triggered when an Excel/CSV file is dropped on `/upload`.
  - `pro_upgrade_click`: Triggered when a user clicks "Upgrade to Pro" or submits JazzCash/EasyPaisa proof.
  - `blog_read_complete`: Triggered on 75% scroll depth across `/blog/<slug>`.

---

## 3. High-Quality B2B & Software Directory Citations

Submitting to reputable SaaS directories creates initial high-domain-authority (DA 60-90) referring backlinks:

| Directory | Category / Anchor | Priority |
| :--- | :--- | :--- |
| **Product Hunt** | Launch post: "AI Data Analyst Pro — SQL & Excel Analytics Co-Pilot" | High |
| **AlternativeTo** | List as alternative to Julius AI, ChatGPT Advanced Data Analysis, Tableau | High |
| **SaaSHub** | Data Analytics & Business Intelligence Category | High |
| **SourceForge** | Business Analytics Software Directory | Medium |
| **Crunchbase** | Company & Product Profile: AI Data Analyst Pro | Medium |
| **GitHub** | Public Showcase / Documentation & SDK Repositories | High |
| **Pakistani Tech Communities** | Pakistan Software Houses Association (P@SHA), TechJuice, ProPakistani SaaS showcase | High |

---

## 4. Backlink Building Strategy (White-Hat)

### 4.1 Data Science & Finance Blog Outreach
- **Topic:** *"The Hidden Cost of Broken Excel Spreadsheets in Mid-Sized Businesses"*
- Pitch guest articles to corporate finance and operations publications (CFO.com, Towards Data Science, Medium publication hubs) linking to our educational guides:
  - Anchor: `how to analyze Excel data with AI` -> `https://aidataanalystpro.co.uk/blog/how-to-analyze-excel-data-with-ai`
  - Anchor: `sales forecasting tool` -> `https://aidataanalystpro.co.uk/features/ai-sales-forecasting`

### 4.2 Free Data Cleaners & Micro-Tools
- Release interactive free utilities (e.g., free online CSV delimiter fixer, column anonymizer for Excel) that link back organically from developer forums and Reddit (`r/excel`, `r/datascience`, `r/smallbusiness`).

---

## 5. 8-Week Content Publishing Calendar

Publishing consistent, search-intent driven articles strengthens topical authority in Google's semantic graph:

| Week | Article Slug | Focus Keyword | Target Audience |
| :--- | :--- | :--- | :--- |
| **Week 1** | `how-to-analyze-excel-data-with-ai` | how to analyze Excel data with AI | Financial Analysts & Accountants |
| **Week 2** | `how-to-find-why-sales-dropped` | how to find why sales dropped | E-Commerce & Retail Store Owners |
| **Week 3** | `how-to-read-profit-and-loss-from-sales-sheet` | read profit and loss from sales sheet | Startup Founders & SME Managers |
| **Week 4** | `best-charts-for-sales-data` | best charts for sales data | Marketing & Growth Directors |
| **Week 5** | `how-to-clean-messy-csv-data` | how to clean messy CSV data | Operations Leads & Admin Staff |
| **Week 6** | `data-analysis-for-small-business-owners` | data analysis for small business owners | Sole Proprietors & SME Retailers |
| **Week 7** | `what-is-natural-language-data-analysis` | natural language data analysis | Tech Leads & Product Managers |
| **Week 8** | `how-to-forecast-sales-from-past-data` | how to forecast sales from past data | Supply Chain & Inventory Planners |

---

## 6. Regular Technical Health Checks (Monthly)
1. **Broken Link Audit:** Run a headless crawler every 30 days to ensure 0 broken internal links (0 `404` errors).
2. **Sitemap Integrity:** Verify that new blog articles appear in `sitemap.xml` with matching `<lastmod>` timestamps.
3. **Core Web Vitals:** Verify that Largest Contentful Paint (LCP) remains under 2.5 seconds on mobile 4G networks.
4. **SSL & Security Headers:** Maintain A+ SSL rating with TLS 1.3, HSTS, and strict `X-Content-Type-Options: nosniff`.
