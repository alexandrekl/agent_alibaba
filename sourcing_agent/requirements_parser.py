import re
from typing import Any, Dict


def parse_requirements(query: str) -> Dict[str, Any]:
    """Turn a plain-English request into structured sourcing parameters."""
    cleaned_query = query.strip()

    keyword = re.sub(
        r"^(find|search|look for|show me)\s+",
        "",
        cleaned_query,
        flags=re.IGNORECASE,
    ).strip()
    keyword = re.sub(
        r"\b(under|below|up to|at most|max(?:imum)?|for|with)\b.*$",
        "",
        keyword,
        flags=re.IGNORECASE,
    ).strip()
    keyword = re.sub(r"\s+", " ", keyword).strip()

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
