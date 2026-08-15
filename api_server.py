from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from urllib.parse import urlsplit, urlunsplit

from sourcing_agent.workflow import create_app

app = FastAPI(title="Alibaba sourcing API")
DB_PATH = Path(__file__).resolve().parent / "sourcing_reviews.db"


class SearchRequest(BaseModel):
    sku_num: str = Field(..., min_length=1)
    rfq_text: str = Field(..., min_length=1)


class DecisionItem(BaseModel):
    listing_url: str = ""
    listing_title: str = ""
    supplier: str = ""
    accepted: bool = True
    reason: str = ""


class SubmitRequest(BaseModel):
    sku_num: str = Field(..., min_length=1)
    decisions: List[DecisionItem] = Field(..., min_items=1)


class AdminListingCreate(BaseModel):
    sku_num: str = Field(..., min_length=1)
    listing_url: str = ""
    listing_title: str = Field(..., min_length=1)
    supplier: str = ""
    status: str = "approved"
    notes: str = ""


class AdminListingUpdate(BaseModel):
    status: str
    notes: str = ""


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS listing_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku_num TEXT NOT NULL,
                listing_url TEXT NOT NULL,
                listing_title TEXT NOT NULL,
                supplier TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        columns = conn.execute("PRAGMA table_info(listing_decisions)").fetchall()
        existing_columns = {row[1] for row in columns}
        if "reason" not in existing_columns:
            conn.execute(
                "ALTER TABLE listing_decisions ADD COLUMN reason TEXT NOT NULL DEFAULT ''"
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_listing_decisions_sku ON listing_decisions(sku_num)"
        )
        conn.commit()


# Drops query string/fragment: Alibaba appends volatile per-scrape tokens (priceId,
# selectedCarrierCode, etc.) that differ between requests for the same physical listing.
def _normalize_listing_url(url: str) -> str:
    url = str(url).strip()
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).lower()

def _shortlist_from_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return result.get("accepted_shortlist") or result.get("shortlist") or []

# Reads either raw listing dicts (title/companyName) or DB history dicts (listing_title/supplier).
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


# Same key rules as _listing_identity(); kept separate since DB rows are read as columns, not a dict.
def _listing_key_from_row(listing_url: str, listing_title: str, supplier: str) -> str:
    if listing_url:
        return _normalize_listing_url(listing_url)
    base = f"{str(supplier).strip().lower()}|{str(listing_title).strip().lower()}"
    return base


def _get_prior_decisions(sku_num: str) -> List[str]:
    with _connect_db() as conn:
        rows = conn.execute(
            "SELECT listing_url, listing_title, supplier FROM listing_decisions WHERE sku_num = ?",
            (sku_num,),
        ).fetchall()
    excluded: List[str] = []
    for row in rows:
        excluded.append(_listing_key_from_row(row["listing_url"], row["listing_title"], row["supplier"]))
    return excluded


def _get_decisions_for_sku(sku_num: str) -> Dict[str, List[Dict[str, str]]]:
    with _connect_db() as conn:
        rows = conn.execute(
            "SELECT id, listing_url, listing_title, supplier, decision, reason, created_at FROM listing_decisions WHERE sku_num = ? ORDER BY created_at DESC",
            (sku_num,),
        ).fetchall()
    accepted: List[Dict[str, str]] = []
    rejected: List[Dict[str, str]] = []
    for row in rows:
        payload = {
            "id": row["id"],
            "listing_url": row["listing_url"],
            "listing_title": row["listing_title"],
            "supplier": row["supplier"],
            "reason": row["reason"],
            "timestamp": row["created_at"],
        }
        if row["decision"] == "accepted":
            accepted.append(payload)
        else:
            rejected.append(payload)
    return {"accepted": accepted, "rejected": rejected}


def _decision_value_from_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"approved", "accepted"}:
        return "accepted"
    if normalized == "rejected":
        return "rejected"
    raise HTTPException(status_code=422, detail="Status must be approved or rejected")


def _get_all_sku_numbers() -> List[str]:
    with _connect_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT sku_num FROM listing_decisions ORDER BY sku_num"
        ).fetchall()
    return [str(row["sku_num"]) for row in rows]


init_db()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/search")
def search(request: SearchRequest) -> Dict[str, Any]:
    # This is the main backend entry point for the sourcing workflow.
    # The graph below orchestrates: requirement parsing, data fetching,
    # ranking, reviews, and optional agentic search refinement.
    prior_decisions = _get_decisions_for_sku(request.sku_num)
    accepted_history = prior_decisions["accepted"]
    rejected_history = prior_decisions["rejected"]

    # LangGraph graph: this is where the backend delegates the "agentic" behavior.
    # The graph combines deterministic sourcing logic with iteration on user/agent feedback.
    # 1st exclusion point: api_sourcing_node() inside the graph screens accepted_history/rejected_history
    # out of the fresh fetch before ranking.
    graph = create_app()
    result = graph.invoke({
        "raw_query": request.rfq_text,
        "sku_num": request.sku_num,
        "accepted_listings": accepted_history,
        "rejected_listings": rejected_history,
        "logs": [],
    })
    shortlist = _shortlist_from_result(result)

    # 2nd exclusion point (outer safety net): re-checks the graph's output against the DB in case
    # ranking/refinement reintroduced a previously decided listing.
    prior_exclusions = set(_get_prior_decisions(request.sku_num))

    filtered = []
    for item in shortlist:
        identity = _listing_identity(item)
        if identity in prior_exclusions:
            continue
        filtered.append(item)

    return {
        "sku_num": request.sku_num,
        "shortlist": filtered[:5],
        "count": len(filtered[:5]),
        "accepted": accepted_history,
        "rejected": rejected_history,
    }


@app.post("/api/v1/submit_decisions")
def submit_decisions(request: SubmitRequest) -> Dict[str, Any]:
    if not request.decisions:
        raise HTTPException(status_code=400, detail="At least one decision is required")

    with _connect_db() as conn:
        for decision in request.decisions:
            listing_url = (decision.listing_url or "").strip()
            listing_title = (decision.listing_title or "").strip()
            supplier = (decision.supplier or "").strip()
            reason = (decision.reason or "").strip()
            if not listing_url and not listing_title:
                continue

            decision_value = "accepted" if decision.accepted else "rejected"
            existing = conn.execute(
                "SELECT id FROM listing_decisions WHERE sku_num = ? AND listing_url = ? AND listing_title = ? AND supplier = ?",
                (request.sku_num, listing_url, listing_title, supplier),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE listing_decisions SET decision = ?, reason = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (decision_value, reason, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO listing_decisions (sku_num, listing_url, listing_title, supplier, decision, reason) VALUES (?, ?, ?, ?, ?, ?)",
                    (request.sku_num, listing_url, listing_title, supplier, decision_value, reason),
                )
        conn.commit()

    decisions = _get_decisions_for_sku(request.sku_num)
    return {
        "sku_num": request.sku_num,
        "accepted": decisions["accepted"],
        "rejected": decisions["rejected"],
    }


@app.get("/api/v1/decisions/{sku_num}")
def get_decisions(sku_num: str) -> Dict[str, Any]:
    return _get_decisions_for_sku(sku_num)


@app.get("/api/v1/skus")
def get_skus() -> Dict[str, Any]:
    return {"skus": _get_all_sku_numbers()}


@app.post("/api/v1/admin/listings")
def create_admin_listing(request: AdminListingCreate) -> Dict[str, Any]:
    decision = _decision_value_from_status(request.status)
    with _connect_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO listing_decisions (sku_num, listing_url, listing_title, supplier, decision, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                request.sku_num.strip(),
                request.listing_url.strip(),
                request.listing_title.strip(),
                request.supplier.strip(),
                decision,
                request.notes.strip(),
            ),
        )
        conn.commit()
    return {"id": cursor.lastrowid, "sku_num": request.sku_num.strip()}


@app.patch("/api/v1/admin/listings/{listing_id}")
def update_admin_listing(listing_id: int, request: AdminListingUpdate) -> Dict[str, Any]:
    decision = _decision_value_from_status(request.status)
    with _connect_db() as conn:
        cursor = conn.execute(
            """
            UPDATE listing_decisions
            SET decision = ?, reason = ?, created_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (decision, request.notes.strip(), listing_id),
        )
        conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"id": listing_id, "status": request.status.lower(), "notes": request.notes.strip()}


@app.delete("/api/v1/admin/listings/{listing_id}")
def delete_admin_listing(listing_id: int, sku_num: str) -> Dict[str, Any]:
    with _connect_db() as conn:
        cursor = conn.execute(
            "DELETE FROM listing_decisions WHERE id = ? AND sku_num = ?",
            (listing_id, sku_num),
        )
        conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Listing not found for this SKU")
    return {"id": listing_id, "deleted": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
