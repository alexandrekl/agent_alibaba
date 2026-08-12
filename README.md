# Alibaba sourcing workflow

This project runs a small LangGraph-based sourcing workflow against live Alibaba listings via Apify.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy the sample environment file and fill in your keys:
   ```bash
   cp .env.example .env
   ```
4. Run the workflow with an RFQ file:
   ```bash
   python sourcing_graph.py rfq_OCulink.txt
   ```

## Model configuration

The project reads provider settings from environment variables so the model can be swapped without changing the workflow logic.

Example `.env` values:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
```

You can also switch to the deterministic fallback by setting:

```env
LLM_PROVIDER=mock
```

## Notes

- The workflow reads the RFQ from a text file.
- It ranks product listings by relevance to the RFQ title and prints a shortlist with listing URLs.
- Keep secrets in a local `.env` file and do not commit it to source control.
