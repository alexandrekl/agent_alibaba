from typing import Any, Dict, List, TypedDict


class SourcingState(TypedDict, total=False):
    raw_query: str
    search_payload: Dict[str, Any]
    raw_results: List[Dict[str, Any]]
    shortlist: List[Dict[str, Any]]
    user_feedback: List[Dict[str, Any]]
    rejected_listings: List[Dict[str, Any]]
    accepted_shortlist: List[Dict[str, Any]]
    review_round: int
    refinement_notes: str
    logs: List[str]
