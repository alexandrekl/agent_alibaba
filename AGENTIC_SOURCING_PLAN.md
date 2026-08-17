# Agentic sourcing plan

## Reset-safe summary

This project is a hybrid sourcing workflow that preserves the existing deterministic engine while adding an iterative human-in-the-loop refinement layer.

Core idea:
- keep the current rule-based fetch/rank logic as the baseline
- after retrieving the top shortlist, ask the user to review each listing
- when the user rejects some listings, synthesize tighter search criteria from that feedback
- rerun the sourcing flow with improved keywords, constraints, and supplier-quality filters
- continue until the shortlist is acceptable or a review cap is reached

Implementation status:
- review and refinement state fields are present in the workflow model
- the graph supports a shortlist review loop and conditional refinement branch
- the LLM layer is abstracted behind a provider interface so models can be swapped cleanly
- if no API key is configured, a deterministic fallback keeps the system operational

Primary files:
- `sourcing_agent/workflow.py`: graph orchestration and refinement logic
- `sourcing_agent/llm_client.py`: model abstraction and fallback provider
- `sourcing_agent/models.py`: workflow state schema
- `tests/test_sourcing_graph.py`: regression coverage for review and refinement behavior

The Streamlit client and FastAPI server also provide persisted SKU decisions and an admin panel for manual listing creation, note editing, status changes, and deletion.

Recommended starting model:
- `gpt-4.1-mini` as the default low-cost, reliable first model

Command to validate:
- `cd /Users/aligo/git_repos/agent_alibaba && pytest tests/test_sourcing_graph.py -q`

## Goal

Keep the current deterministic sourcing engine as the reliable baseline and layer in a user-driven refinement loop powered by an LLM or LLM-like reasoning layer. The agent narrows the search progressively based on what the user rejects while preserving the original RFQ and hard constraints like price and MOQ.

## Next phase: bounded tool-calling search planner

Add a limited autonomous planning step before the existing Alibaba fetch node. The planner may split an ambiguous RFQ into a small number of targeted search queries, but deterministic code remains responsible for executing calls, enforcing constraints, ranking listings, persisting decisions, and final approval.

This is not a replacement for the current hybrid workflow. It is a query-planning layer that makes technically ambiguous RFQs more useful without allowing unbounded model behavior.

### Initial capability

- Interpret mutually exclusive or alternative product families in an RFQ as separate search paths.
- For example, search `SlimSAS SFF-8654 8i to 2x U.2 SFF-8639` independently from `MCIO 8i (SFF-TA-1016) to 2x U.2 SFF-8639`.
- Propose compact Alibaba-ready keywords while preserving hard constraints such as price and MOQ.
- Return structured query plans only; it cannot call external services or write to the database directly.

Implementation status:
- `plan_search_queries_node` runs between requirement analysis and the Alibaba fetch.
- The provider adapter accepts at most three structured keywords and falls back to deterministic splitting of explicit `OR` alternatives.
- Python applies parsed price and MOQ limits to every planned query, executes the existing Apify client calls, and deduplicates overlapping listing results before ranking.
- Focused regression coverage validates alternative planning, fallback behavior, constraint preservation, bounded multi-query execution, and deduplication.
- When an OpenAI provider is configured, it now receives a native `search_alibaba` function schema and can choose the next search after inspecting prior tool results.
- The application executes at most three native tool calls per sourcing run, supplies immutable RFQ price/MOQ constraints, and falls back to the validated deterministic plan when native tool calling is unavailable.
- Provider failures, including rate limits and unavailable credits, fall back to deterministic planning and refinement so a configured but unavailable model does not stop sourcing.

### Tool boundary and guardrails

The application owns all real tool calls. The planner can request `search_alibaba` with structured arguments, and Python validates and executes that request through the existing Apify client. For an OpenAI provider, the model's tool result is returned to the conversation before it decides whether another bounded search is useful.

- Maximum 3 planned queries per RFQ.
- Maximum 5 results per query, using the existing Apify limit.
- Price and MOQ limits are inherited from the parsed RFQ and cannot be relaxed by the model.
- The existing duplicate screening, deterministic ranking, review cap, SQLite history, and admin approval/rejection controls remain mandatory.
- The planner cannot create, edit, approve, reject, or delete listings.
- If no LLM key is configured or the plan is invalid, use a deterministic single-query fallback.

### Proposed graph change

```text
analyze_requirements
   -> plan_search_queries
   -> fetch_alibaba_data (once per validated query)
   -> vet_and_rank_suppliers
   -> review_shortlist
   -> refine_search_terms (when rejected and under the review cap)
   -> fetch_alibaba_data
```

The initial implementation should use a small adapter behind the existing provider abstraction, validate its structured output, and add tests for alternative connector-family planning plus fallback behavior. Listing detail inspection and clarification questions are deferred until the planner is stable.

## Current state

The workflow in `sourcing_agent/workflow.py` already handles:
- requirement parsing
- Alibaba fetch
- supplier ranking
- shortlist generation

- a human review-and-refinement loop
- an LLM-backed refinement adapter with a deterministic fallback
- persisted decisions and admin listing management
- exclusion of previously decided listings from new searches

## Verification

Run the focused test suite after implementation:

```bash
cd /Users/aligo/git_repos/agent_alibaba && pytest tests/test_sourcing_graph.py
```

## Scope decisions

- Keep the current deterministic engine and its ranking logic intact.
- Add the LLM-backed refinement layer as a loop around it, not as a replacement.
- If no API key is configured, the workflow should still operate with a deterministic heuristic fallback so local development remains stable.
