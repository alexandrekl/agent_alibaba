import re
from typing import Any, Dict


def parse_requirements(query: str) -> Dict[str, Any]:
    """Turn a plain-English request into structured sourcing parameters."""
    cleaned_query = query.strip()

    keyword_candidates = []
    for line in cleaned_query.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^\*\s*", "", line)
        line = re.sub(r"^SKU\s*\d+:\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"^Volume:\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"^Specs:\s*", "", line, flags=re.IGNORECASE)
        line = line.strip()
        if line:
            keyword_candidates.append(line)

    keyword = " ".join(keyword_candidates).strip()
    if not keyword:
        keyword = cleaned_query.strip() or "products"

    keyword = re.sub(r"\s+", " ", keyword).strip()
    keyword = re.sub(r"\b(?:volume|units|length|specs|sku)\b", "", keyword, flags=re.IGNORECASE)
    keyword = re.sub(r"\s+", " ", keyword).strip()

    if not keyword:
        keyword = cleaned_query.strip() or "products"

    max_price_match = re.search(r"(?:under|below|up to|at most|<=)\s*\$?\s*(\d+(?:\.\d+)?)", query, re.IGNORECASE)
    max_price = float(max_price_match.group(1)) if max_price_match else None

    moq_match = re.search(r"(?:mqo|moq)\s*(?:of|=|:)?\s*(\d+)", query, re.IGNORECASE)
    max_moq = int(moq_match.group(1)) if moq_match else 1000

    return {
        "keyword": keyword,
        "max_price_usd": max_price,
        "max_moq": max_moq,
    }
