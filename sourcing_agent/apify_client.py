import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from apify_client import ApifyClient as ApifySDKClient


def call_alibaba_proxy_api(
    keyword: str,
    limit: int = 5,
    max_price: Optional[float] = None,
    max_moq: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Run the configured Apify actor and return the scraped Alibaba listings."""
    api_token = os.getenv("APIFY_API_TOKEN")
    if not api_token:
        raise RuntimeError("APIFY_API_TOKEN is not configured")

    actor_id = os.getenv("APIFY_ACTOR_ID", "scraper-engine/alibaba-scraper")
    client = ApifySDKClient(api_token)

    try:
        search_url = f"https://www.alibaba.com/trade/search?fsb=y&IndexArea=product_en&CatId=&SearchText={quote(keyword)}"
        actor_input = {
            "urls": [search_url],
            "maxItems": limit,
            "proxyConfiguration": {"useApifyProxy": True},
        }
        if max_price is not None:
            actor_input["maxPriceUsd"] = max_price
        if max_moq is not None:
            actor_input["maxMoq"] = max_moq

        run = client.actor(actor_id).call(run_input=actor_input)
        dataset_id = getattr(run, "default_dataset_id", None)
        if not dataset_id:
            raise RuntimeError("Apify run did not return a dataset ID")

        items = list(client.dataset(dataset_id).list_items().items)
        if items:
            return items

        raise RuntimeError("Apify returned no items")
    except Exception as exc:
        raise RuntimeError(f"Apify sourcing failed: {exc}") from exc


def _parse_moq(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        digits = re.findall(r"\d+", value)
        if digits:
            return int(digits[0])
    return 999999


def _parse_price(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return 999999.0
