"""
R-Phase 9.4: typo / Roman-Urdu tolerant Ask AI, end to end on a real encrypted dataset
(temporary SQLite + vault), every answer checked against pandas on the same rows.
No LLM key: only the deterministic layers answer.
"""
import numpy as np
import pandas as pd
import pytest
from cryptography.fernet import Fernet

from ai import gemini
from database import connection as db
from utils import dataset_vault as vault

TABLE = "fuzzy_sales_u1"


def _frame(n=1200, seed=7):
    rng = np.random.default_rng(seed)
    qty = rng.integers(1, 10, n)
    price = rng.choice([150.0, 250.0, 400.0, 900.0, 35.0], n)
    sales = (qty * price).round(2)
    return pd.DataFrame({
        "OrderID": np.arange(1, n + 1),
        "OrderDate": (pd.to_datetime("2025-01-01") + pd.to_timedelta(rng.integers(0, 540, n), unit="D")).strftime("%Y-%m-%d"),
        "Region": rng.choice(["North", "South", "East", "West"], n),
        "City": rng.choice(["Lahore", "Karachi", "Islamabad", "Multan", "Quetta"], n),
        "Product": rng.choice(["Laptop", "Phone", "Tablet", "Monitor", "Printer"], n),
        "Status": rng.choice(["Delivered", "Cancelled", "Returned"], n, p=[0.8, 0.1, 0.1]),
        "Quantity": qty, "UnitPrice": price, "Sales": sales,
        "Profit": (sales * rng.uniform(0.05, 0.3, n)).round(2),
    })


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    db_file = str(tmp_path_factory.mktemp("fuzzy") / "fuzzy.db")
    mp.setenv("DATASET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    mp.setattr(db, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    import uploads.data_loader as dl
    mp.setattr(dl, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    mp.setattr(vault, "_schema_ready", False)
    mp.setattr(vault, "_meta_cache", {})
    mp.delenv("OPENROUTER_API_KEY", raising=False)
    vault._mirrors.clear()
    try:
        from utils.cache import system_cache
        if system_cache:
            system_cache.clear()
    except Exception:
        pass
    df = _frame()
    from uploads.data_loader import create_table, insert_dataframe
    create_table(TABLE, df.copy())
    insert_dataframe(TABLE, df)
    cols = db.get_table_columns(TABLE)
    yield df, cols
    mp.undo()


def _ask(q, cols):
    r = gemini.execute_sql_with_retry(q, TABLE, cols, max_retries=0)
    return r


def _num(d):
    for c in d.columns:
        v = pd.to_numeric(d[c], errors="coerce").dropna()
        if len(v):
            return float(v.iloc[0])


def _by(d, key):
    k = next(c for c in d.columns if c.lower() == key.lower())
    v = next(c for c in d.columns if c != k and pd.to_numeric(d[c], errors="coerce").notna().all())
    return {str(a): round(float(b), 2) for a, b in zip(d[k], d[v])}


def _grp(df, key, val, how="sum"):
    return {k: round(float(v), 2) for k, v in getattr(df.groupby(key)[val], how)().items()}


CASES = [
    ("totl revnue by citty", lambda s, d: _by(d, "City") == _grp(s, "City", "Sales")),
    ("top 5 prodcuts by sles", lambda s, d: list(d["Product"])[:5] == list(s.groupby("Product").Sales.sum().sort_values(ascending=False).index[:5])),
    ("averge proft by regoin", lambda s, d: all(abs(_by(d, "Region")[k] - v) < 0.02 for k, v in _grp(s, "Region", "Profit", "mean").items())),
    ("how mny orders wer canceled", lambda s, d: _num(d) == (s.Status == "Cancelled").sum()),
    ("totel quantty of laptp", lambda s, d: _num(d) == s[s.Product == "Laptop"].Quantity.sum()),
    ("lahor ki totl sales kitni hai", lambda s, d: abs(_num(d) - s[s.City == "Lahore"].Sales.sum()) < 0.5),
    ("sab se zyada proffit kis prodct ka hai", lambda s, d: str(d.iloc[0]["Product"]) == s.groupby("Product").Profit.sum().idxmax()),
    ("karachi mein kitne ordrs hain", lambda s, d: _num(d) == (s.City == "Karachi").sum()),
    ("mahine ke hisab se sales trnd dikhao", lambda s, d: len(d) == s.OrderDate.str[:7].nunique()),
    ("max unit prise", lambda s, d: _num(d) == s.UnitPrice.max()),
    ("sales by staus", lambda s, d: _by(d, "Status") == _grp(s, "Status", "Sales")),
    ("which regin has hihgest profit", lambda s, d: str(d.iloc[0]["Region"]) == s.groupby("Region").Profit.sum().idxmax()),
]


@pytest.mark.parametrize("q,check", CASES, ids=[c[0] for c in CASES])
def test_misspelled_questions_answer_correctly(data, q, check):
    src, cols = data
    r = _ask(q, cols)
    assert r["success"] and not r.get("is_out_of_domain"), r.get("error")
    assert check(src, r["df"]), (q, r.get("sql"))


def test_corrections_are_stated_not_silent(data):
    _, cols = data
    r = _ask("lahor ki totl sales kitni hai", cols)
    assert any("Lahore" in n for n in r["assumptions"])
    r = _ask("totl revnue by citty", cols)
    assert any("no 'revnue' column" in n and "Sales" in n for n in r["assumptions"])


def test_ambiguous_value_is_not_guessed(monkeypatch):
    import ai.value_matcher as vm
    monkeypatch.setattr(vm, "get_column_vocabulary", lambda t, si=None: {"City": ["Malir", "Mardan", "Lahore"], "Store": ["Malik"]})
    q, notes = gemini.correct_question_with_notes("sales in mali", ["City", "Store", "Sales"], "t")
    # 'mali' is equally close to Malir and Malik: left as typed, user asked to choose
    assert "mali" in q.split()
    assert any("could mean" in n and "Malir" in n and "Malik" in n for n in notes)


def test_roman_urdu_words_are_not_turned_into_values(monkeypatch):
    import ai.value_matcher as vm
    monkeypatch.setattr(vm, "get_column_vocabulary", lambda t, si=None: {"Product": ["Camera", "Laptop"]})
    q, notes = gemini.correct_question_with_notes("mera mood kharab hai kya karun", ["Product", "Sales"], "t")
    assert q == "mera mood kharab hai kya karun" and notes == []
    assert gemini.is_out_of_domain_question(q, ["Product", "Sales"]) is True


@pytest.mark.parametrize("q", ["kal mausam kaisa hoga", "who is the prime minister of pakistan",
                               "write a python function to sort a list", "tell me a joke"])
def test_off_topic_gets_exact_refusal(data, q):
    _, cols = data
    r = _ask(q, cols)
    assert r["is_out_of_domain"] is True
    assert r["error"] == "I'm designed to analyze your uploaded dataset only. Please ask a question about your data."
