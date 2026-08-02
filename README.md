# Alibaba sourcing workflow

This project runs a small LangGraph-based sourcing workflow against live Alibaba listings via Apify.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the workflow with an RFQ file:
   ```bash
   python sourcing_graph.py rfq_OCulink.txt
   ```

## Notes

- The workflow reads the RFQ from a text file.
- It ranks product listings by relevance to the RFQ title and prints a shortlist with listing URLs.
