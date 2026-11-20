"""
One place that decides the public origin used in every absolute link the app sends
out (email links, password reset, newsletter unsubscribe, Google OAuth redirect URI,
sitemap/robots).

Order of precedence:
  1. PUBLIC_BASE_URL env (e.g. https://aidataanalystpro.co.uk). Always use this in
     production.
  2. The current request's own origin (request.url_root). Behind Nginx this is only
     correct when the proxy hop is trusted: wsgi.py's Waitress setup trusts the one
     loopback hop, and TRUSTED_PROXY_COUNT (see run.py) adds Werkzeug's ProxyFix for
     other servers. Untrusted X-Forwarded-* headers are never read here directly.
  3. Outside a request (scheduler jobs) with nothing configured: http://127.0.0.1:<PORT>,
     and a warning is logged, because such a link is useless to a recipient.
"""
import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]"}


def _configured_base():
    value = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if value.startswith(("http://", "https://")):
        return value
    return ""


def is_local_url(url):
    host = (urlsplit(url).hostname or "").lower()
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


def is_production():
    """True when the deployment says it is production (explicit env or secure cookies)."""
    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if env == "production":
        return True
    return os.getenv("FORCE_SECURE_COOKIES", "false").strip().lower() == "true"


def public_base_url():
    """Scheme + host (no trailing slash) for links that leave this server."""
    configured = _configured_base()
    if configured:
        return configured
    try:
        from flask import has_request_context, request
        if has_request_context():
            return request.url_root.rstrip("/")
    except Exception:  # pragma: no cover - flask always importable in the app
        pass
    fallback = f"http://127.0.0.1:{os.getenv('PORT', '5000')}"
    logger.warning(
        "PUBLIC_BASE_URL is not set and there is no request to derive a host from; "
        "building a link with %s, which recipients cannot open.", fallback
    )
    return fallback


def external_url(endpoint, **values):
    """Absolute URL for a Flask endpoint, on the public origin (never url_for(_external))."""
    from flask import url_for
    values.pop("_external", None)
    return public_base_url() + url_for(endpoint, **values)


def check_public_base_url_config(log=None):
    """Startup check. Returns a list of warning strings (also logged at WARNING/ERROR)."""
    log = log or logger
    problems = []
    configured = _configured_base()
    raw = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not raw:
        problems.append(
            "PUBLIC_BASE_URL is not set. Email links (OTP, verification, password reset, "
            "reports) and the Google redirect URI will use the request host instead. "
            "Set PUBLIC_BASE_URL=https://aidataanalystpro.co.uk in production."
        )
    elif not configured:
        problems.append(f"PUBLIC_BASE_URL={raw!r} is not an http(s) URL and is ignored.")
    elif is_local_url(configured) and is_production():
        problems.append(
            f"PUBLIC_BASE_URL={configured} points at this machine while running in "
            "production. Every emailed link will be broken for recipients."
        )
    elif configured.startswith("http://") and is_production():
        problems.append(f"PUBLIC_BASE_URL={configured} is plain http in production; use https.")
    for msg in problems:
        if is_production():
            log.error("!!! CONFIG WARNING: %s", msg)
        else:
            log.warning("CONFIG WARNING: %s", msg)
        print("=" * 78 + f"\n[CONFIG WARNING] {msg}\n" + "=" * 78)
    return problems
