import re
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from .apify_client import call_alibaba_proxy_api
from .models import SourcingState
from .requirements_parser import parse_requirements


def requirement_analyzer_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node A] Analyzing sourcing requirements ---")
    query = state.get("raw_query", "")
    payload = parse_requirements(query)
    return {
        "search_payload": payload,
        "logs": state.get("logs", []) + [f"Parsed request into keyword='{payload['keyword']}' with max MOQ {payload['max_moq']}."],
    }


def api_sourcing_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node B] Querying Alibaba data layer ---")
    payload = state["search_payload"]
    results = call_alibaba_proxy_api(
        keyword=payload["keyword"],
        max_price=payload.get("max_price_usd"),
        max_moq=payload.get("max_moq"),
    )
    return {
        "raw_results": results,
        "logs": state["logs"] + [f"Fetched {len(results)} raw listing hits from the data layer."],
    }


def supplier_vet_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node C] Vetting and ranking suppliers ---")
    raw_list = state.get("raw_results", [])
    search_payload = state["search_payload"]
    keyword = search_payload.get("keyword", "")
    max_allowed_moq = search_payload.get("max_moq", 1000)
    max_price = search_payload.get("max_price_usd")

    ranked_candidates = []
    for item in raw_list:
        verified = bool(item.get("verified", True))
        moq_value = item.get("moq") or item.get("moqV2") or item.get("minimum_order_quantity")
        moq = _parse_moq(moq_value) if moq_value not in (None, "") else None
        price = _parse_price(item.get("price_usd") or item.get("price") or item.get("priceUsd"))
        trade_assurance = _parse_trade_assurance(item)

        if max_price is not None and price > max_price:
            continue
        if moq is not None and moq > max_allowed_moq:
            continue

        title = (item.get("title") or item.get("productName") or item.get("name") or "").strip()
        relevance_score = _score_title_relevance(title, keyword)

        item_with_signal = dict(item)
        item_with_signal["trade_assurance"] = trade_assurance
        item_with_signal["verified"] = verified
        item_with_signal["relevance_score"] = relevance_score
        ranked_candidates.append((
            -relevance_score,
            0 if trade_assurance else 1,
            0 if verified else 1,
            price,
            moq if moq is not None else 999999,
            item_with_signal,
        ))

    ranked_candidates.sort(key=lambda entry: (
        entry[0],
        entry[1],
        entry[2],
        entry[3],
        entry[4],
    ))

    filtered_shortlist = [entry[5] for entry in ranked_candidates]

    return {
        "shortlist": filtered_shortlist,
        "logs": state["logs"] + ["Completed relevance-first ranking, with trade-assurance and verification as secondary signals."],
    }


def _score_title_relevance(title: str, keyword: str) -> int:
    if not title or not keyword:
        return 0

    normalized_title = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    normalized_keyword = re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip()

    if not normalized_keyword:
        return 0

    title_tokens = set(normalized_title.split())
    keyword_tokens = set(normalized_keyword.split())

    overlap = len(title_tokens & keyword_tokens)
    if overlap > 0:
        return overlap * 10

    if normalized_keyword in normalized_title:
        return 20

    return 0


def _parse_trade_assurance(item: Dict[str, Any]) -> bool:
    for key in ("tradeAssurance", "trade_assurance", "tradeAssuranceBadge", "trade_assurance_badge", "tradeAssured", "trade_assured"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "y", "1", "verified", "available", "badge"}:
                return True
            if normalized in {"false", "no", "n", "0", "none", "not available"}:
                return False
    return False


def _parse_moq(value: Any) -> int:
    from .apify_client import _parse_moq as parse_moq
    return parse_moq(value)


def _parse_price(value: Any) -> float:
    from .apify_client import _parse_price as parse_price
    return parse_price(value)


def build_workflow() -> StateGraph:
    workflow = StateGraph(SourcingState)
    workflow.add_node("analyze_requirements", requirement_analyzer_node)
    workflow.add_node("fetch_alibaba_data", api_sourcing_node)
    workflow.add_node("vet_and_rank_suppliers", supplier_vet_node)
    workflow.set_entry_point("analyze_requirements")
    workflow.add_edge("analyze_requirements", "fetch_alibaba_data")
    workflow.add_edge("fetch_alibaba_data", "vet_and_rank_suppliers")
    workflow.add_edge("vet_and_rank_suppliers", END)
    return workflow


def create_app():
    workflow = build_workflow()
    return workflow.compile()
