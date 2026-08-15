import re
from typing import Any, Dict


def parse_requirements(query: str) -> Dict[str, Any]:
    """Turn a plain-English request into structured sourcing parameters."""
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return {"keyword": "products", "max_price_usd": None, "max_moq": 1000}

    normalized = cleaned_query.replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"(?i)\b(?:sku|volume|specs|requirements|rfq|request)\b[:\-]*", " ", normalized)
    normalized = re.sub(r"(?i)\b(?:rohs|compliant|compliance|certified|silver|gold|white|black)\b", " ", normalized)
    normalized = re.sub(r"[^a-zA-Z0-9\-\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    tokens = []
    for token in normalized.split():
        clean = token.strip("-_.")
        if len(clean) <= 1:
            continue
        if clean.lower() in {"under", "below", "up", "to", "at", "most", "for", "with", "and", "or", "of", "the", "a", "an", "please", "need"}:
            continue
        tokens.append(clean)

    keyword = " ".join(tokens[:12]).strip()
    if not keyword:
        keyword = "products"

    max_price_match = re.search(r"(?:under|below|up to|at most|<=)\s*\$?\s*(\d+(?:\.\d+)?)", query, re.IGNORECASE)
    max_price = float(max_price_match.group(1)) if max_price_match else None

    moq_match = re.search(r"(?:mqo|moq)\s*(?:of|=|:)?\s*(\d+)", query, re.IGNORECASE)
    max_moq = int(moq_match.group(1)) if moq_match else 1000

    return {
        "keyword": keyword,
        "max_price_usd": max_price,
        "max_moq": max_moq,
    }
