import re
from typing import Any, Dict, List

from langgraph.graph import END, StateGraph

from .apify_client import call_alibaba_proxy_api, _parse_moq, _parse_price
from .llm_client import plan_search_queries_with_provider, refine_search_with_provider, run_search_tool_loop
from .models import SourcingState
from .requirements_parser import parse_requirements
from urllib.parse import urlsplit, urlunsplit


def requirement_analyzer_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node A] Analyzing sourcing requirements ---")
    query = state.get("raw_query", "")
    payload = state.get("search_payload") or parse_requirements(query)
    preserved_logs = state.get("logs", [])

    if state.get("search_payload"):
        payload = dict(state["search_payload"])
        preserved_logs = preserved_logs + [
            f"Reused existing search payload with keyword='{payload.get('keyword')}' and max MOQ {payload.get('max_moq')}."]
    else:
        payload = parse_requirements(query)
        preserved_logs = preserved_logs + [
            f"Parsed request into keyword='{payload['keyword']}' with max MOQ {payload['max_moq']}."
        ]

    return {
        "search_payload": payload,
        "search_queries": state.get("search_queries", []),
        "shortlist": [],
        "user_feedback": [],
        "accepted_listings": state.get("accepted_listings", []),
        "rejected_listings": state.get("rejected_listings", []),
        "accepted_shortlist": [],
        "review_round": int(state.get("review_round", 0)) if state.get("search_payload") else 0,
        "refinement_notes": state.get("refinement_notes", ""),
        "logs": preserved_logs,
    }


def plan_search_queries_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node B] Planning bounded Alibaba searches ---")
    payload = state["search_payload"]
    planned_keywords = plan_search_queries_with_provider(
        raw_query=state.get("raw_query", ""),
        search_payload=payload,
    )
    queries = [
        {
            "keyword": keyword,
            "max_price_usd": payload.get("max_price_usd"),
            "max_moq": payload.get("max_moq"),
        }
        for keyword in planned_keywords[:3]
        if keyword.strip()
    ]
    if not queries:
        queries = [dict(payload)]

    return {
        "search_queries": queries,
        "logs": state["logs"] + [f"Planned {len(queries)} bounded Alibaba search query or queries."],
    }


def api_sourcing_node(state: SourcingState) -> Dict[str, Any]:
    print("--- [Node C] Querying Alibaba data layer ---")
    queries = state.get("search_queries") or [state["search_payload"]]
    payload = state["search_payload"]

    def execute_search(keyword: str) -> List[Dict[str, Any]]:
        return call_alibaba_proxy_api(
            keyword=keyword,
            max_price=payload.get("max_price_usd"),
            max_moq=payload.get("max_moq"),
        )

    results = run_search_tool_loop(
        raw_query=state.get("raw_query", ""),
        search_payload=payload,
        execute_search=execute_search,
    )
    if results is None:
        results = []
        for query in queries[:3]:
            results.extend(
            call_alibaba_proxy_api(
                keyword=query["keyword"],
                max_price=query.get("max_price_usd"),
                max_moq=query.get("max_moq"),
            )
            )

    # accepted_listings/rejected_listings may mix DB-shaped dicts (listing_title/supplier, from the
    # first call) with raw-shaped dicts (title/companyName, appended by review_shortlist_node below).
    accepted_history = state.get("accepted_listings", [])
    rejected_history = state.get("rejected_listings", [])
    seen_identities = {
        _listing_identity(item)
        for item in accepted_history + rejected_history
        if isinstance(item, dict)
    }
    filtered_results = []
    for item in results:
        identity = _listing_identity(item)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        filtered_results.append(item)

    return {
        "raw_results": filtered_results,
        "logs": state["logs"] + [f"Fetched {len(filtered_results)} raw listing hits from the data layer after screening prior SKU decisions."],
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
        price = _parse_price(item.get("price_usd") or item.get("price") or item.get("priceUsD"))
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

    # accepted/rejected are raw-shaped (title/companyName); see api_sourcing_node's note on mixed shapes.
    return {
        "accepted_listings": state.get("accepted_listings", []) + accepted,
        "rejected_listings": state.get("rejected_listings", []) + rejected,
        "accepted_shortlist": accepted or shortlist[:5],
        "logs": state.get("logs", []) + [f"Reviewed {len(shortlist[:5])} listing candidates with user feedback."],
    }


def refine_search_terms_node(state: SourcingState) -> Dict[str, Any]:
    # Agentic refinement step: convert user feedback and rejected listing reasons into a
    # better search payload. This is the part of the workflow that acts like a reasoning layer
    # rather than a pure deterministic filter.
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
    keyword = str(provider_result.get("keyword") or search_payload.get("keyword") or raw_query or "products").strip()
    updated_payload["keyword"] = _normalize_search_keyword(keyword, raw_query)
    updated_payload["max_moq"] = provider_result.get("max_moq") if provider_result.get("max_moq") is not None else search_payload.get("max_moq")
    updated_payload["max_price_usd"] = provider_result.get("max_price_usd") if provider_result.get("max_price_usd") is not None else search_payload.get("max_price_usd")

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
        updated_payload["keyword"] = _append_keyword_modifier(updated_payload["keyword"], "rohs")

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
        "search_queries": [],
        "review_round": new_round,
        "refinement_notes": provider_result["notes"],
        "logs": state.get("logs", []) + [f"Refined search terms via model adapter and moved to review round {new_round}."],
    }


def should_refine_search(state: SourcingState) -> str:
    # This guard decides whether the agentic search-refinement loop should iterate again.
    # If the user rejects previous candidates and the review round has not exceeded the cap,
    # the graph re-runs the agent to rephrase the query and tighten the search.
    rejected = state.get("rejected_listings", [])
    review_round = int(state.get("review_round", 0))
    if rejected and review_round < 3:
        return "refine_search_terms"
    return END


def _normalize_search_keyword(keyword: str, raw_query: str) -> str:
    text = keyword or raw_query or "products"
    cleaned = re.sub(r"[^a-z0-9\s\-_.]+", " ", text.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\b(?:volume|units|length|specs|sku|product|products)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "products"
    return cleaned


def _normalize_listing_url(url: str) -> str:
    url = str(url).strip()
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).lower()


def _listing_identity(item: Dict[str, Any]) -> str:
    listing_url = (
        item.get("listing_url")
        or item.get("url")
        or item.get("productUrl")
        or item.get("product_url")
        or item.get("detailUrl")
        or item.get("sourceUrl")
        or item.get("link")
        or ""
    )
    if listing_url:
        return _normalize_listing_url(listing_url)
    company = str(item.get("companyName") or item.get("supplier") or "").strip().lower()
    title = str(item.get("title") or item.get("listing_title") or item.get("productName") or item.get("name") or "").strip().lower()
    return f"{company}|{title}"


def _append_keyword_modifier(keyword: str, modifier: str) -> str:
    tokens = [part for part in re.split(r"\s+", keyword.strip()) if part]
    modifier_tokens = [part for part in re.split(r"\s+", modifier.strip()) if part]
    for part in modifier_tokens:
        if part.lower() not in {token.lower() for token in tokens}:
            tokens.append(part)
    return " ".join(tokens)


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


def build_workflow() -> StateGraph:
    workflow = StateGraph(SourcingState)
    workflow.add_node("analyze_requirements", requirement_analyzer_node)
    workflow.add_node("plan_search_queries", plan_search_queries_node)
    workflow.add_node("fetch_alibaba_data", api_sourcing_node)
    workflow.add_node("vet_and_rank_suppliers", supplier_vet_node)
    workflow.add_node("review_shortlist", review_shortlist_node)
    workflow.add_node("refine_search_terms", refine_search_terms_node)
    workflow.set_entry_point("analyze_requirements")
    workflow.add_edge("analyze_requirements", "plan_search_queries")
    workflow.add_edge("plan_search_queries", "fetch_alibaba_data")
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
