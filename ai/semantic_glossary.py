"""
Semantic Business Glossary System
Allows users to define custom business terminology and maps them into SQL query planning:
- GMV = Gross Merchandise Value / Total Sales
- Net Sales = Sales after returns
- Active Customer = Customer with order within last 90 days
"""

import re

DEFAULT_BUSINESS_GLOSSARY = {
    "gmv": "Total revenue or order value across all transactions",
    "net sales": "Completed order sales minus returned order values",
    "active customer": "Customers who placed orders within the dataset date range",
    "high value customer": "Customers with total spend in the top 10th percentile",
    "return rate": "Percentage of returned orders over total orders"
}

_user_glossaries = {}


def get_user_glossary(user_id: int) -> dict:
    """Returns the user-defined business glossary dictionary."""
    return _user_glossaries.get(user_id, DEFAULT_BUSINESS_GLOSSARY.copy())


def set_user_glossary_term(user_id: int, term: str, definition: str):
    """Sets a custom term definition for a user."""
    if user_id not in _user_glossaries:
        _user_glossaries[user_id] = DEFAULT_BUSINESS_GLOSSARY.copy()
    _user_glossaries[user_id][term.lower().strip()] = definition.strip()


def inject_glossary_context(question: str, user_id: int = None) -> str:
    """
    Expands business jargon terms in user question using the semantic glossary.
    """
    glossary = get_user_glossary(user_id) if user_id else DEFAULT_BUSINESS_GLOSSARY
    q_lower = question.lower()

    glossary_hints = []
    for term, definition in glossary.items():
        if re.search(r"\b" + re.escape(term) + r"\b", q_lower):
            glossary_hints.append(f"Business Term '{term}': {definition}")

    if glossary_hints:
        return f"{question}\n\n[Semantic Business Glossary Context: {'; '.join(glossary_hints)}]"
    return question
