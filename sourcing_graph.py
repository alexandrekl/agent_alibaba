from dotenv import load_dotenv

from sourcing_agent.apify_client import call_alibaba_proxy_api
from sourcing_agent.run import main

load_dotenv()  # This looks for your .env file and loads the variables

__all__ = ["call_alibaba_proxy_api", "main"]


if __name__ == "__main__":
    main()
