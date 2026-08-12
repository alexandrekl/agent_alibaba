import re
from typing import Any, Dict, List

from langgraph.graph import END, StateGraph

from .apify_client import call_alibaba_proxy_api
from .llm_client import refine_search_with_provider
from .models import SourcingState
from .requirements_parser import parse_requirements


def requirement_analyzer_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node A] Analyzing sourcing requirements ---")
    query = state.get("raw_query", "")
    payload = parse_requirements(query)
    return {
        "search_payload": payload,
        "shortlist": [],
        "user_feedback": state.get("user_feedback", []),
        "rejected_listings": [],
        "accepted_shortlist": [],
        "review_round": 0,
        "refinement_notes": "",
        "logs": state.get("logs", []) + [f"Parsed request into keyword='{payload['keyword']}' with max MOQ {payload['max_moq']}."]
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
        "accepted_shortlist": filtered_shortlist[:5],
        "logs": state["logs"] + ["Completed relevance-first ranking, with trade-assurance and verification as secondary signals."],
    }


def review_shortlist_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node D] Reviewing top listings with the user ---")
    shortlist = state.get("shortlist", [])
    user_feedback = state.get("user_feedback", [])

    rejected: List[Dict[str, Any]] = []
    accepted: List[Dict[str, Any]] = []

    for feedback in user_feedback:
        raw_index = int(feedback.get("index", 0))
        supplier_index = raw_index - 1 if raw_index > 0 else 0
        keep = bool(feedback.get("keep", True))
        if 0 <= supplier_index < len(shortlist):
            supplier = dict(shortlist[supplier_index])
            supplier["review_reason"] = feedback.get("reason", "")
            if keep:
                accepted.append(supplier)
            else:
                rejected.append(supplier)

    if not user_feedback:
        accepted = shortlist[:5]

    return {
        "accepted_shortlist": accepted or shortlist[:5],
        "rejected_listings": rejected,
        "logs": state.get("logs", []) + [f"Reviewed {len(shortlist[:5])} listing candidates with user feedback."],
    }


def refine_search_terms_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node E] Refining search terms from user feedback ---")
    search_payload = dict(state.get("search_payload", {}))
    raw_query = state.get("raw_query", "")
    user_feedback = state.get("user_feedback", [])
    rejected = state.get("rejected_listings", [])
    review_round = int(state.get("review_round", 0))

    provider_result = refine_search_with_provider(
        raw_query=raw_query,
        search_payload=search_payload,
        user_feedback=user_feedback,
        rejected_listings=rejected,
    )

    updated_payload = dict(search_payload)
    updated_payload["keyword"] = provider_result["keyword"]
    updated_payload["max_moq"] = provider_result["max_moq"]
    updated_payload["max_price_usd"] = provider_result["max_price_usd"]

    combined_feedback = " ".join(
        [
            str(item.get("reason", "")).strip()
            for item in user_feedback
            if str(item.get("reason", "")).strip()
        ]
        + [
            str(item.get("review_reason", "")).strip()
            for item in rejected
            if str(item.get("review_reason", "")).strip()
        ]
    ).lower()

    if "rohs" in combined_feedback or "compliance" in combined_feedback:
        updated_payload["keyword"] = f"rohs {updated_payload['keyword']}".strip()

    if "too expensive" in combined_feedback or "price" in combined_feedback or "budget" in combined_feedback:
        current_limit = float(updated_payload.get("max_price_usd") or 0.0)
        if current_limit > 0:
            updated_payload["max_price_usd"] = round(max(1.0, current_limit * 0.85), 2)
        elif search_payload.get("max_price_usd") is not None:
            updated_payload["max_price_usd"] = round(max(1.0, float(search_payload.get("max_price_usd")) * 0.85), 2)

    if "moq" in combined_feedback or "minimum order" in combined_feedback or "order quantity" in combined_feedback:
        current_limit = int(updated_payload.get("max_moq") or search_payload.get("max_moq") or 1000)
        updated_payload["max_moq"] = max(1, current_limit // 2)

    new_round = review_round + 1
    return {
        "search_payload": updated_payload,
        "review_round": new_round,
        "refinement_notes": provider_result["notes"],
        "logs": state.get("logs", []) + [f"Refined search terms via model adapter and moved to review round {new_round}."],
    }


def should_refine_search(state: SourcingState) -> str:
    rejected = state.get("rejected_listings", [])
    review_round = int(state.get("review_round", 0))
    if rejected and review_round < 3:
        return "refine_search_terms"
    return END


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
    workflow.add_node("review_shortlist", review_shortlist_node)
    workflow.add_node("refine_search_terms", refine_search_terms_node)
    workflow.set_entry_point("analyze_requirements")
    workflow.add_edge("analyze_requirements", "fetch_alibaba_data")
    workflow.add_edge("fetch_alibaba_data", "vet_and_rank_suppliers")
    workflow.add_edge("vet_and_rank_suppliers", "review_shortlist")
    workflow.add_conditional_edges(
        "review_shortlist",
        should_refine_search,
        {
            "refine_search_terms": "refine_search_terms",
            END: END,
        },
    )
    workflow.add_edge("refine_search_terms", "fetch_alibaba_data")
    return workflow


def create_app():
    workflow = build_workflow()
    return workflow.compile()
