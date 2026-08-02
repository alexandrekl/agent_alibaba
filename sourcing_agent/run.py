import argparse
import os
from typing import Any, Dict

from .workflow import create_app


def run_sourcing_agent(raw_query: str) -> Dict[str, Any]:
    """Run the full sourcing workflow for a single user request."""
    app = create_app()
    return app.invoke({"raw_query": raw_query, "logs": []})


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

    print("\n--- FINAL SHORTLISTED SUPPLIERS ---")
    for idx, supplier in enumerate(final_output.get("shortlist", []), 1):
        print(format_supplier_summary(supplier, idx))


if __name__ == "__main__":
    main()
