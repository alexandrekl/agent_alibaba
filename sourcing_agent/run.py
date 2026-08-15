import argparse
import os
from typing import Any, Dict, List

from .workflow import create_app


def collect_user_feedback(shortlist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ask the user to keep or reject each shortlist listing."""
    feedback: List[Dict[str, Any]] = []
    for idx, supplier in enumerate(shortlist[:5], start=1):
        company = supplier.get("companyName") or supplier.get("supplier") or "Unknown supplier"
        title = supplier.get("title") or supplier.get("productName") or supplier.get("name") or "Untitled listing"
        choice = input(f"Keep {idx}. {company} - {title}? [Y/n]: ").strip().lower()
        keep = choice not in {"n", "no"}
        reason = ""
        if not keep:
            reason = input(f"Reason for rejecting {idx}. {company}? ").strip() or "not suitable"
        feedback.append({
            "index": idx,
            "keep": keep,
            "reason": reason,
        })
    return feedback


def run_sourcing_agent(raw_query: str) -> Dict[str, Any]:
    """Run the full sourcing workflow for a single user request with an interactive shortlist review loop."""
    app = create_app()
    current_state: Dict[str, Any] = {"raw_query": raw_query, "logs": []}

    for _ in range(3):
        result = app.invoke(current_state)
        shortlist = result.get("accepted_shortlist") or result.get("shortlist") or []
        if not shortlist:
            return result

        user_feedback = collect_user_feedback(shortlist)
        if not user_feedback:
            return result

        if all(item.get("keep", True) for item in user_feedback):
            return result

        current_state = {
            "raw_query": raw_query,
            "search_payload": result.get("search_payload", {}),
            "review_round": result.get("review_round", 0),
            "user_feedback": [],
            "rejected_listings": [],
            "logs": result.get("logs", []),
        }

        if result.get("search_payload"):
            current_state["search_payload"] = result["search_payload"]

    return app.invoke(current_state)


def format_supplier_summary(supplier: Dict[str, Any], idx: int) -> str:
    company = supplier.get("companyName") or supplier.get("supplier") or "Unknown supplier"
    title = supplier.get("title") or supplier.get("productName") or supplier.get("name") or "Untitled listing"
    listing_url = (
        supplier.get("listing_url")
        or supplier.get("url")
        or supplier.get("productUrl")
        or supplier.get("product_url")
        or supplier.get("detailUrl")
        or supplier.get("sourceUrl")
        or supplier.get("link")
    )
    label = title if not listing_url else f"{title} | {listing_url}"

    raw_price = supplier.get("price_usd") or supplier.get("price") or "N/A"
    price_text = raw_price if raw_price != "N/A" else "unknown"
    currency_text = supplier.get("currency") or supplier.get("currencyText") or "unknown"
    moq = supplier.get("moq") or supplier.get("moqV2") or "N/A"
    verified = supplier.get("verified", True)
    trade_assurance = supplier.get("trade_assurance") or supplier.get("tradeAssurance") or False
    trade_label = "Yes" if trade_assurance else "No"
    relevance_score = supplier.get("relevance_score", 0)
    return (
        f"{idx}. {company} | Listing: {label} | Price: {price_text} | Currency: {currency_text} | MOQ: {moq} | "
        f"Verified: {verified} | Trade Assurance: {trade_label} | Relevance: {relevance_score}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the sourcing workflow against an RFQ text file")
    parser.add_argument(
        "rfq_path",
        nargs="?",
        default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "rfq.txt"),
        help="Path to the RFQ text file (defaults to rfq.txt)",
    )
    args = parser.parse_args(argv)

    rfq_path = args.rfq_path
    if not os.path.isabs(rfq_path):
        rfq_path = os.path.join(os.getcwd(), rfq_path)

    with open(rfq_path, "r", encoding="utf-8") as rfq_file:
        initial_query = rfq_file.read().strip()

    final_output = run_sourcing_agent(initial_query)

    accepted = final_output.get("accepted_shortlist") or final_output.get("shortlist") or []
    print("\n--- FINAL SHORTLISTED SUPPLIERS ---")
    if not accepted:
        print("No shortlisted suppliers were accepted.")
    else:
        for idx, supplier in enumerate(accepted, 1):
            print(format_supplier_summary(supplier, idx))

    if final_output.get("refinement_notes"):
        print("\n--- REFINEMENT NOTES ---")
        print(final_output["refinement_notes"])

    if final_output.get("logs"):
        print("\n--- WORKFLOW LOG ---")
        for line in final_output["logs"]:
            print(f"- {line}")


if __name__ == "__main__":
    main()
