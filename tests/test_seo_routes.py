"""
SEO & Public Marketing Routes Test Suite.
Verifies response status, JSON-LD schema, canonical tags, sitemap, and robots.txt.
"""
import pytest
from flask import Flask
from frontend.routes import frontend
from utils.blog_data import BLOG_ARTICLES


@pytest.fixture
def seo_app():
    from auth import auth_bp
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = "test-seo-secret"
    app.config["WTF_CSRF_ENABLED"] = False
    app.register_blueprint(frontend)
    app.register_blueprint(auth_bp, url_prefix="/auth")

    @app.context_processor
    def inject_context():
        import site_config
        from utils.public_url import public_base_url
        base = public_base_url().rstrip("/") + "/"
        return {
            "site_config": site_config,
            "public_root": base,
            "canonical_url": "https://aidataanalystpro.co.uk/",
            "support_email": site_config.support_email(),
            "now_year": 2026,
            "csrf_token": lambda: "dummy-csrf-token",
        }

    return app


@pytest.fixture
def client(seo_app):
    return seo_app.test_client()


def test_public_seo_routes_ok(client):
    routes = [
        "/features/excel-csv-ai-analysis",
        "/features/ask-ai-data",
        "/features/ai-sales-forecasting",
        "/compare/ai-analyst-vs-human-analyst",
        "/use-cases/retail-ecommerce",
        "/use-cases/pharmacy-analytics",
        "/use-cases/manufacturing-production",
        "/use-cases/hospital-clinic-reporting",
        "/use-cases/small-business",
        "/blog",
    ]
    for r in routes:
        resp = client.get(r)
        assert resp.status_code == 200, f"Route {r} failed with status {resp.status_code}"
        html = resp.data.decode("utf-8")
        assert "application/ld+json" in html, f"Missing JSON-LD structured data in {r}"
        assert "canonical" in html, f"Missing canonical link in {r}"


def test_all_blog_articles_ok(client):
    import html as html_lib
    assert len(BLOG_ARTICLES) == 8, "Expected exactly 8 blog articles in BLOG_ARTICLES"
    for slug, article in BLOG_ARTICLES.items():
        resp = client.get(f"/blog/{slug}")
        assert resp.status_code == 200, f"Blog article {slug} failed with {resp.status_code}"
        raw_html = resp.data.decode("utf-8")
        unescaped_html = html_lib.unescape(raw_html)
        assert article["title"] in unescaped_html, f"Title missing in {slug}"
        assert "application/ld+json" in raw_html, f"Schema missing in {slug}"
        assert f"/blog/{slug}" in raw_html, f"Canonical slug missing in {slug}"


def test_sitemap_xml(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.mimetype == "application/xml"
    content = resp.data.decode("utf-8")
    assert "<urlset" in content
    assert "/auth/login" not in content, "Auth login should not be in sitemap"
    assert "/auth/signup" not in content, "Auth signup should not be in sitemap"
    assert "https://aidataanalystpro.co.uk/features/excel-csv-ai-analysis" in content
    assert "https://aidataanalystpro.co.uk/compare/ai-analyst-vs-human-analyst" in content
    assert "https://aidataanalystpro.co.uk/blog/how-to-analyze-excel-data-with-ai" in content


def test_robots_txt(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    content = resp.data.decode("utf-8")
    assert "Disallow: /auth/" in content
    assert "Disallow: /dashboard" in content
    assert "Disallow: /upload" in content
    assert "Disallow: /admin/" in content
    assert "Sitemap: https://aidataanalystpro.co.uk/sitemap.xml" in content
