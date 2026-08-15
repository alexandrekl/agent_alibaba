import os
from typing import Any, Dict, List, Tuple

import requests
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Sourcing reviewer", layout="wide")

if "shortlist" not in st.session_state:
    st.session_state.shortlist = []
if "accepted" not in st.session_state:
    st.session_state.accepted = []
if "rejected" not in st.session_state:
    st.session_state.rejected = []
if "mode" not in st.session_state:
    st.session_state.mode = "initial"
if "final_approved" not in st.session_state:
    st.session_state.final_approved = []
if "sku_input" not in st.session_state:
    st.session_state.sku_input = ""
if "rfq_input" not in st.session_state:
    st.session_state.rfq_input = ""


def call_search(sku_num: str, rfq_text: str) -> Dict[str, Any]:
    response = requests.post(
        f"{API_BASE_URL}/api/v1/search",
        json={"sku_num": sku_num, "rfq_text": rfq_text},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def call_submit(sku_num: str, decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    response = requests.post(
        f"{API_BASE_URL}/api/v1/submit_decisions",
        json={"sku_num": sku_num, "decisions": decisions},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def call_skus() -> List[str]:
    response = requests.get(f"{API_BASE_URL}/api/v1/skus", timeout=120)
    response.raise_for_status()
    return response.json().get("skus", [])


def call_decisions(sku_num: str) -> Dict[str, List[Dict[str, str]]]:
    response = requests.get(f"{API_BASE_URL}/api/v1/decisions/{sku_num}", timeout=120)
    response.raise_for_status()
    return response.json()


def call_admin_create(listing: Dict[str, str]) -> Dict[str, Any]:
    response = requests.post(
        f"{API_BASE_URL}/api/v1/admin/listings", json=listing, timeout=120
    )
    response.raise_for_status()
    return response.json()


def call_admin_update(listing_id: int, status: str, notes: str) -> Dict[str, Any]:
    response = requests.patch(
        f"{API_BASE_URL}/api/v1/admin/listings/{listing_id}",
        json={"status": status, "notes": notes},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def call_admin_delete(listing_id: int, sku_num: str) -> Dict[str, Any]:
    response = requests.delete(
        f"{API_BASE_URL}/api/v1/admin/listings/{listing_id}",
        params={"sku_num": sku_num},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def listing_title(item: Dict[str, Any]) -> str:
    return str(item.get("title") or item.get("productName") or item.get("name") or "Untitled listing")


def listing_company(item: Dict[str, Any]) -> str:
    return str(item.get("companyName") or item.get("supplier") or "Unknown supplier")


def listing_url(item: Dict[str, Any]) -> str | None:
    return (
        item.get("listing_url")
        or item.get("url")
        or item.get("productUrl")
        or item.get("product_url")
        or item.get("detailUrl")
        or item.get("sourceUrl")
        or item.get("link")
    )


st.title("Alibaba sourcing review")
st.caption("Thin client: review shortlist items and decide whether to continue sourcing or stop with the final approved list.")

existing_skus = []
try:
    existing_skus = call_skus()
except Exception:  # pragma: no cover - UI only
    existing_skus = []

if existing_skus:
    sku_choice = st.selectbox(
        "SKU number",
        options=existing_skus + ["Type a new SKU..."],
        index=0,
        help="Select an existing SKU or choose to type a new one.",
    )
    if sku_choice == "Type a new SKU...":
        sku_num = st.text_input("New SKU number", key="sku_input", placeholder="Type SKU here")
    else:
        sku_num = sku_choice
        st.session_state.sku_input = sku_num
else:
    sku_num = st.text_input("SKU number", key="sku_input", placeholder="Type SKU here")
    st.session_state.sku_input = sku_num

if sku_num and sku_num in existing_skus:
    try:
        decision_data = call_decisions(sku_num)
        approved_rows = decision_data.get("accepted", [])
        if approved_rows:
            st.subheader(f"Approved listings for SKU {sku_num}")
            approved_df = [{
                "Title": row.get("listing_title") or "Untitled listing",
                "Supplier": row.get("supplier") or "Unknown supplier",
                "URL": row.get("listing_url") or "",
                "Reason": row.get("reason") or "",
            } for row in approved_rows]
            st.dataframe(approved_df, use_container_width=True)
        else:
            st.info(f"No listings have been approved for SKU {sku_num} yet.")
    except Exception:  # pragma: no cover - UI only
        pass

rfq_text = st.text_area(
    "RFQ / search terms",
    key="rfq_input",
    height=160,
    placeholder="Enter product type, connector, compliance, target price, MOQ, etc.",
)

if sku_num.strip():
    with st.expander("Admin panel"):
        st.caption("Manage the persisted listings for this SKU.")
        with st.form("add_manual_listing", clear_on_submit=True):
            st.markdown("#### Add listing manually")
            manual_title = st.text_input("Listing title")
            manual_supplier = st.text_input("Supplier")
            manual_url = st.text_input("Listing URL")
            manual_status = st.selectbox("Status", ("Approved", "Rejected"))
            manual_notes = st.text_area("Notes", height=80)
            add_listing = st.form_submit_button("Add listing")

            if add_listing:
                try:
                    call_admin_create(
                        {
                            "sku_num": sku_num.strip(),
                            "listing_title": manual_title,
                            "supplier": manual_supplier,
                            "listing_url": manual_url,
                            "status": manual_status,
                            "notes": manual_notes,
                        }
                    )
                    st.success("Listing added.")
                    st.rerun()
                except Exception as exc:  # pragma: no cover - UI only
                    st.error(f"Could not add listing: {exc}")

        try:
            admin_decisions = call_decisions(sku_num.strip())
            admin_rows = [
                ("Approved", row) for row in admin_decisions.get("accepted", [])
            ] + [
                ("Rejected", row) for row in admin_decisions.get("rejected", [])
            ]
            if admin_rows:
                st.markdown("#### Edit persisted listings")
                for current_status, row in admin_rows:
                    listing_id = row["id"]
                    title = row.get("listing_title") or "Untitled listing"
                    supplier = row.get("supplier") or "Unknown supplier"
                    with st.form(f"edit_listing_{listing_id}"):
                        st.markdown(f"**{supplier} | {title}**")
                        updated_status = st.selectbox(
                            "Status",
                            ("Approved", "Rejected"),
                            index=0 if current_status == "Approved" else 1,
                            key=f"status_{listing_id}",
                        )
                        updated_notes = st.text_area(
                            "Notes",
                            value=row.get("reason") or "",
                            height=80,
                            key=f"notes_{listing_id}",
                        )
                        save_listing = st.form_submit_button("Save changes")
                        delete_listing = st.form_submit_button("Delete listing")

                        try:
                            if save_listing:
                                call_admin_update(listing_id, updated_status, updated_notes)
                                st.success("Listing updated.")
                                st.rerun()
                            if delete_listing:
                                call_admin_delete(listing_id, sku_num.strip())
                                st.success("Listing deleted.")
                                st.rerun()
                        except Exception as exc:  # pragma: no cover - UI only
                            st.error(f"Could not update listing: {exc}")
            else:
                st.info("No persisted listings for this SKU yet.")
        except Exception as exc:  # pragma: no cover - UI only
            st.error(f"Could not load listings: {exc}")

if st.button("Fetch shortlist") and sku_num.strip() and rfq_text.strip():
    try:
        data = call_search(sku_num.strip(), rfq_text.strip())
        st.session_state.shortlist = data.get("shortlist", [])
        st.session_state.accepted = data.get("accepted", [])
        st.session_state.rejected = data.get("rejected", [])
        st.session_state.mode = "review"
        st.session_state.final_approved = list(st.session_state.accepted)
        st.success(f"Loaded {len(st.session_state.shortlist)} new listing(s) for SKU {sku_num}.")
    except Exception as exc:  # pragma: no cover - UI only
        st.error(f"Search failed: {exc}")

if st.session_state.mode == "final":
    st.subheader("Final approved list")
    if st.session_state.final_approved:
        for idx, row in enumerate(st.session_state.final_approved, start=1):
            title = row.get("listing_title") or "Untitled listing"
            supplier = row.get("supplier") or "Unknown supplier"
            url = row.get("listing_url") or ""
            reason = row.get("reason") or "No reason recorded"
            st.markdown(f"### {idx}. {supplier} — {title}")
            if url:
                st.markdown(f"[Open listing]({url})")
            st.write(f"Reason: {reason}")
            st.divider()
    else:
        st.warning("No approved listings were saved for this SKU. The final approved list is empty.")

    if st.button("Back to initial search page"):
        st.session_state.shortlist = []
        st.session_state.accepted = []
        st.session_state.rejected = []
        st.session_state.final_approved = []
        st.session_state.mode = "initial"
        st.rerun()

if st.session_state.shortlist and st.session_state.mode == "review":
    st.subheader("Review 5 listings")
    decisions: List[Dict[str, Any]] = []

    with st.form("review_decisions"):
        for idx, item in enumerate(st.session_state.shortlist, start=1):
            company = listing_company(item)
            title = listing_title(item)
            url = listing_url(item)

            with st.container():
                st.markdown(f"### {idx}. {company} — {title}")
                if url:
                    st.markdown(f"[Open listing]({url})")
                st.write(f"Price: {item.get('price_usd') or item.get('price') or 'N/A'}")
                st.write(f"MOQ: {item.get('moq') or item.get('moqV2') or 'N/A'}")
                st.write(f"Trade assurance: {'Yes' if item.get('trade_assurance') or item.get('tradeAssurance') else 'No'}")

                choice = st.radio(
                    f"Decision for {company}",
                    ("Accept", "Reject"),
                    key=f"decision_{idx}_{company}",
                    horizontal=True,
                )
                reason = st.text_input(
                    "Reason",
                    key=f"reason_{idx}_{company}",
                    placeholder="Optional short note",
                )
                decisions.append(
                    {
                        "listing_url": url or "",
                        "listing_title": title,
                        "supplier": company,
                        "accepted": choice == "Accept",
                        "reason": reason,
                    }
                )
                st.divider()

        st.info("Edit the RFQ below if needed, then choose whether to save and continue or stop.")
        revised_rfq = st.text_area(
            "Edit RFQ before rerun",
            value=rfq_text,
            height=120,
            key="revised_rfq_before_rerun",
            help="This text is used when you click Save and Continue Search. You can refine the search terms before the graph reruns.",
        )

        continue_button = st.form_submit_button("Save and Continue Search", type="primary")
        stop_button = st.form_submit_button("Save and Stop Search")

        if continue_button or stop_button:
            payload = []
            for item in decisions:
                payload.append(
                    {
                        "listing_url": item["listing_url"],
                        "listing_title": item["listing_title"],
                        "supplier": item["supplier"],
                        "accepted": item["accepted"],
                        "reason": item["reason"],
                    }
                )
            try:
                result = call_submit(sku_num.strip(), payload)
                st.session_state.accepted = result.get("accepted", [])
                st.session_state.rejected = result.get("rejected", [])
                st.session_state.final_approved = list(st.session_state.accepted)

                if continue_button:
                    # Reuse the existing search endpoint so the same LangGraph pipeline runs again
                    # with the latest accepted/rejected history for this SKU and any edited RFQ text.
                    edited_rfq = st.session_state.get("revised_rfq_before_rerun", "") or rfq_text

                    data = call_search(sku_num.strip(), edited_rfq.strip())
                    st.session_state.shortlist = data.get("shortlist", [])
                    st.session_state.accepted = data.get("accepted", [])
                    st.session_state.rejected = data.get("rejected", [])
                    st.session_state.final_approved = list(st.session_state.accepted)
                    st.session_state.mode = "review"
                    st.success("Decisions saved and the existing /api/v1/search flow was rerun with the saved SKU history.")
                else:
                    st.session_state.shortlist = []
                    st.session_state.mode = "final"
                    st.success("Decisions saved. Final approved list is ready.")
            except Exception as exc:  # pragma: no cover - UI only
                st.error(f"Submit failed: {exc}")

if st.session_state.accepted or st.session_state.rejected:
    st.subheader("Persisted decisions for this SKU")
    if st.session_state.accepted:
        st.markdown("#### Accepted")
        for row in st.session_state.accepted:
            st.write(f"- {row.get('listing_title')} | {row.get('supplier')} | {row.get('listing_url')}")
    if st.session_state.rejected:
        st.markdown("#### Rejected")
        for row in st.session_state.rejected:
            st.write(f"- {row.get('listing_title')} | {row.get('supplier')} | {row.get('listing_url')}")
