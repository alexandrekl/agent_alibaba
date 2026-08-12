import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sourcing_agent.apify_client import call_alibaba_proxy_api
from sourcing_agent.run import format_supplier_summary
from sourcing_agent.workflow import (
    refine_search_terms_node,
    review_shortlist_node,
    supplier_vet_node,
)


class SourcingGraphTests(unittest.TestCase):
    @patch("sourcing_agent.apify_client.os.getenv", return_value=None)
    def test_missing_token_raises(self, _mock_getenv):
        with self.assertRaises(RuntimeError):
            call_alibaba_proxy_api("glass bottles")

    @patch("sourcing_agent.apify_client.os.getenv", return_value="fake-token")
    @patch("sourcing_agent.apify_client.ApifySDKClient")
    def test_apify_failure_raises(self, mock_client, _mock_getenv):
        mock_client.return_value.actor.return_value.call.side_effect = Exception("boom")

        with self.assertRaises(RuntimeError):
            call_alibaba_proxy_api("glass bottles")

    def test_trade_assurance_ranks_above_non_trade_assurance(self):
        state = {
            "raw_results": [
                {"companyName": "Regular Supplier", "price": "1.20", "verified": False},
                {"companyName": "Trade Assurance Supplier", "price": "1.25", "verified": True, "tradeAssurance": True},
            ],
            "search_payload": {"max_moq": 1000, "max_price_usd": None},
            "logs": [],
        }

        result = supplier_vet_node(state)

        self.assertEqual(result["shortlist"][0]["companyName"], "Trade Assurance Supplier")
        self.assertTrue(result["shortlist"][0]["trade_assurance"])

    def test_review_shortlist_tracks_rejected_listings(self):
        state = {
            "shortlist": [
                {"companyName": "Good Supplier", "title": "RoHS USB cable"},
                {"companyName": "Pricey Supplier", "title": "USB cable"},
                {"companyName": "MOQ Problem", "title": "USB cable"},
            ],
            "user_feedback": [
                {"index": 1, "keep": True, "reason": "good match"},
                {"index": 2, "keep": False, "reason": "too expensive"},
                {"index": 3, "keep": False, "reason": "MOQ above target"},
            ],
            "logs": [],
        }

        result = review_shortlist_node(state)

        self.assertEqual(len(result["rejected_listings"]), 2)
        self.assertEqual(result["rejected_listings"][0]["companyName"], "Pricey Supplier")

    def test_refine_search_terms_uses_user_feedback(self):
        state = {
            "raw_query": "USB-C cable under $20",
            "search_payload": {
                "keyword": "usb c cable",
                "max_price_usd": 20.0,
                "max_moq": 500,
            },
            "user_feedback": [
                {"index": 1, "keep": False, "reason": "not RoHS compliant"},
                {"index": 2, "keep": False, "reason": "MOQ too high"},
            ],
            "rejected_listings": [
                {"companyName": "Bad Supplier", "title": "USB-C cable", "price": "20.50"},
            ],
            "review_round": 0,
            "logs": [],
        }

        result = refine_search_terms_node(state)

        self.assertIn("rohs", result["search_payload"]["keyword"].lower())
        self.assertIn("usb", result["search_payload"]["keyword"].lower())
        self.assertLess(result["search_payload"]["max_moq"], 500)

    def test_currency_display_uses_source_text_when_available(self):
        supplier = {"companyName": "Example", "price": "$2.98-3.97 TL", "currency": "TL"}
        summary = format_supplier_summary(supplier, 1)

        self.assertIn("Currency: TL", summary)
        self.assertIn("Price: $2.98-3.97 TL", summary)

    def test_currency_display_falls_back_to_unknown(self):
        supplier = {"companyName": "Example", "price": "2.98-3.97"}
        summary = format_supplier_summary(supplier, 1)

        self.assertIn("Currency: unknown", summary)


if __name__ == "__main__":
    unittest.main()
