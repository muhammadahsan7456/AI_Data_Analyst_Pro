"""R-Phase 9.8: .env.example lists every variable the code reads, with no real values."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERN = re.compile(r"""os\.(?:getenv|environ\.get)\(\s*['"]([A-Z0-9_]+)['"]|os\.environ\[['"]([A-Z0-9_]+)['"]\]""")


def _code_vars():
    found = set()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in {"venv", ".venv", ".git", "node_modules", "__pycache__"}]
        for name in filenames:
            if name.endswith(".py"):
                text = open(os.path.join(dirpath, name), encoding="utf-8", errors="ignore").read()
                for a, b in PATTERN.findall(text):
                    found.add(a or b)
    return found


def _example():
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    return dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", text, re.M))


def test_every_env_var_is_documented():
    missing = sorted(_code_vars() - set(_example()))
    assert missing == []


def test_new_release_vars_present():
    ex = _example()
    for name in ("INVOICE_COMPANY_NAME", "INVOICE_COMPANY_ADDRESS", "INVOICE_COMPANY_TAX_ID",
                 "RATE_LIMITS_ENABLED", "TRUST_CLOUDFLARE_IP", "MAIL_FROM"):
        assert name in ex


def test_no_real_secrets_in_example():
    ex = _example()
    for name in ("SECRET_KEY", "DATASET_ENCRYPTION_KEY", "DB_PASSWORD", "SMTP_PASSWORD",
                 "OPENROUTER_API_KEY", "GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_ID"):
        assert ex.get(name, "") == "", name
