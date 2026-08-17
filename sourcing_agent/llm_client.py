from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Protocol

from dotenv import load_dotenv

load_dotenv()


class LLMProvider(Protocol):
    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        ...


class MockLLMProvider:
    """Deterministic fallback provider used when no external model is configured."""

    name = "mock"

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        prompt_lower = prompt.lower()
        adjustments: List[str] = []

        if "rohs" in prompt_lower or "compliance" in prompt_lower:
            adjustments.append("Add RoHS / compliance wording to the keyword search.")
        if "moq" in prompt_lower or "minimum order" in prompt_lower:
            adjustments.append("Prefer lower MOQ suppliers and keep the MOQ cap tight.")
        if "verified" in prompt_lower or "trade assurance" in prompt_lower:
            adjustments.append("Prefer verified or trade-assurance suppliers.")
        if "price" in prompt_lower or "budget" in prompt_lower:
            adjustments.append("Lower the price cap modestly if the shortlist is weak.")

        if adjustments:
            return "; ".join(adjustments)
        return "Prefer verified, better aligned suppliers with tighter keyword matching."


class OpenAIChatProvider:
    """Thin adapter around the OpenAI chat client."""

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is not installed. Install it with pip install openai.") from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        completion = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt or "You are a sourcing assistant helping narrow product searches."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        content = completion.choices[0].message.content
        return content.strip() if content else ""

    def run_search_tool_loop(
        self,
        raw_query: str,
        search_payload: Dict[str, Any],
        execute_search: Callable[[str], List[Dict[str, Any]]],
        max_calls: int = 3,
    ) -> List[Dict[str, Any]] | None:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is not installed. Install it with pip install openai.") from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": "You are a sourcing agent. Use search_alibaba when needed. Search no more than three times and preserve the RFQ's technical intent.",
            },
            {
                "role": "user",
                "content": f"Source this RFQ: {raw_query}. Hard constraints are fixed by the application: {json.dumps(search_payload, sort_keys=True)}.",
            },
        ]
        tools = [{
            "type": "function",
            "function": {
                "name": "search_alibaba",
                "description": "Search Alibaba listings using one concise product keyword.",
                "parameters": {
                    "type": "object",
                    "properties": {"keyword": {"type": "string"}},
                    "required": ["keyword"],
                    "additionalProperties": False,
                },
            },
        }]
        results: List[Dict[str, Any]] = []
        calls_made = 0

        while calls_made < max_calls:
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
            )
            message = completion.choices[0].message
            tool_calls = message.tool_calls or []
            if not tool_calls:
                return results or None

            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in tool_calls
                ],
            })
            for tool_call in tool_calls:
                if calls_made >= max_calls:
                    break
                try:
                    arguments = json.loads(tool_call.function.arguments)
                    keyword = str(arguments.get("keyword", "")).strip()
                    if tool_call.function.name != "search_alibaba" or not keyword:
                        raise ValueError("search_alibaba requires a keyword")
                    tool_result = execute_search(keyword)
                    results.extend(tool_result)
                    content = json.dumps(tool_result, ensure_ascii=False, default=str)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    content = json.dumps({"error": str(exc)})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": content,
                })
                calls_made += 1

        return results or None


def get_llm_provider(provider_name: str | None = None) -> LLMProvider:
    provider_name = (provider_name or os.getenv("LLM_PROVIDER", "openai")).lower()
    if provider_name in {"mock", "fallback", "none"}:
        return MockLLMProvider()
    if provider_name in {"openai", "gpt"}:
        if not os.getenv("OPENAI_API_KEY"):
            return MockLLMProvider()
        return OpenAIChatProvider()
    raise ValueError(f"Unsupported LLM provider: {provider_name}")


def format_rejection_summary(user_feedback: Iterable[Dict[str, Any]], rejected_listings: Iterable[Dict[str, Any]]) -> str:
    reasons: List[str] = []
    for item in user_feedback:
        reason = (item.get("reason") or "").strip()
        if reason:
            reasons.append(reason)
    for item in rejected_listings:
        reason = (item.get("review_reason") or "").strip()
        if reason:
            reasons.append(reason)
    return "; ".join(dict.fromkeys(reasons))


def build_refinement_prompt(raw_query: str, search_payload: Dict[str, Any], user_feedback: Iterable[Dict[str, Any]], rejected_listings: Iterable[Dict[str, Any]]) -> str:
    summary = format_rejection_summary(user_feedback, rejected_listings)
    payload_json = json.dumps(search_payload, ensure_ascii=False, sort_keys=True)
    return (
        "You are helping refine a supplier search for an RFQ. "
        "Keep the user’s original intent but improve the search terms and constraints using the feedback below. "
        "Do not invent brand names or unrelated requirements. "
        "Return a compact JSON object with: "
        "{\"keyword\": str, \"max_moq\": int | null, \"max_price_usd\": float | null, \"notes\": str}. "
        f"Original query: {raw_query}. "
        f"Current search payload: {payload_json}. "
        f"User rejection reasons: {summary or 'No specific rejection feedback provided.'}"
    )


def _fallback_search_keywords(raw_query: str, search_payload: Dict[str, Any]) -> List[str]:
    alternatives = re.split(r"\s+(?:or)\s+", raw_query, flags=re.IGNORECASE)
    keywords = [alternative.strip() for alternative in alternatives if alternative.strip()]
    if len(keywords) > 1:
        return keywords[:3]
    return [str(search_payload.get("keyword") or raw_query or "products").strip()]


def build_search_planning_prompt(raw_query: str, search_payload: Dict[str, Any]) -> str:
    payload_json = json.dumps(search_payload, ensure_ascii=False, sort_keys=True)
    return (
        "Plan up to three compact Alibaba search keywords for this sourcing RFQ. "
        "Split explicitly stated alternatives into separate queries when appropriate. "
        "Preserve connector standards, product family, and compatibility terms. "
        "Do not add brands, price limits, MOQ limits, or unsupported requirements. "
        "Return only JSON in this shape: {\"queries\": [{\"keyword\": str}]}. "
        f"Original query: {raw_query}. Parsed hard constraints: {payload_json}."
    )


def plan_search_queries_with_provider(
    raw_query: str,
    search_payload: Dict[str, Any],
    provider: LLMProvider | None = None,
) -> List[str]:
    provider = provider or get_llm_provider()
    prompt = build_search_planning_prompt(raw_query, search_payload)
    system_prompt = (
        "You are a sourcing search planner. Return only valid JSON and plan concise, "
        "technically precise product-search keywords."
    )

    try:
        response = provider.generate(prompt, system_prompt=system_prompt)
        parsed = json.loads(response.strip())
        query_items = parsed.get("queries", []) if isinstance(parsed, dict) else []
        keywords = [
            str(item.get("keyword", "")).strip()
            for item in query_items
            if isinstance(item, dict) and str(item.get("keyword", "")).strip()
        ]
    except Exception:
        keywords = []

    return list(dict.fromkeys(keywords))[:3] or _fallback_search_keywords(raw_query, search_payload)


def run_search_tool_loop(
    raw_query: str,
    search_payload: Dict[str, Any],
    execute_search: Callable[[str], List[Dict[str, Any]]],
    provider: LLMProvider | None = None,
) -> List[Dict[str, Any]] | None:
    provider = provider or get_llm_provider()
    if not isinstance(provider, OpenAIChatProvider):
        return None
    try:
        return provider.run_search_tool_loop(raw_query, search_payload, execute_search)
    except Exception:
        return None


def refine_search_with_provider(
    raw_query: str,
    search_payload: Dict[str, Any],
    user_feedback: Iterable[Dict[str, Any]],
    rejected_listings: Iterable[Dict[str, Any]],
    provider: LLMProvider | None = None,
) -> Dict[str, Any]:
    # This is the actual LLM invocation point for the agentic refinement step.
    # The model receives the original RFQ, the current search payload, and the reasons
    # why prior listings were rejected or not kept.
    provider = provider or get_llm_provider()
    prompt = build_refinement_prompt(raw_query, search_payload, user_feedback, rejected_listings)
    system_prompt = (
        "You are an expert sourcing assistant. Rewrite the search terms to better align with the user's feedback while "
        "preserving the original RFQ intent. Prefer concise, realistic Alibaba search keywords."
    )

    try:
        # The provider may be a mock implementation or an OpenAI-compatible chat client.
        # This indirection keeps model swaps easy and isolates the rest of the code from vendor details.
        response = provider.generate(prompt, system_prompt=system_prompt)
    except Exception:
        response = "{\"keyword\": \"\", \"max_moq\": null, \"max_price_usd\": null, \"notes\": \"\"}"

    try:
        parsed = json.loads(response.strip())
    except (TypeError, ValueError):
        parsed = {"keyword": "", "max_moq": None, "max_price_usd": None, "notes": response.strip()}

    result = {
        "keyword": str(parsed.get("keyword") or search_payload.get("keyword") or raw_query or "products"),
        "max_moq": parsed.get("max_moq") if parsed.get("max_moq") is not None else search_payload.get("max_moq"),
        "max_price_usd": parsed.get("max_price_usd") if parsed.get("max_price_usd") is not None else search_payload.get("max_price_usd"),
        "notes": str(parsed.get("notes") or "Refined with user feedback."),
    }
    return result
