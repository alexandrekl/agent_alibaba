from typing import Any, Dict, List, TypedDict


class SourcingState(TypedDict, total=False):
    raw_query: str
    search_payload: Dict[str, Any]
    raw_results: List[Dict[str, Any]]
    shortlist: List[Dict[str, Any]]
    logs: List[str]
