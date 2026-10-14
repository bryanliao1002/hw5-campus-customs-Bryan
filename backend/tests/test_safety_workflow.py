"""Safety regression tests that operate only on disposable database copies."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from typing import Literal, get_type_hints
from unittest.mock import patch

from pydantic_ai.exceptions import UsageLimitExceeded


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class WorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_root = tempfile.TemporaryDirectory(prefix="campus-customs-safety-")
        cls.temp_dir = Path(cls.temp_root.name)
        cls.test_database = cls.temp_dir / "working.db"
        cls.test_audit = cls.temp_dir / "audit_trail.json"
        cls.test_decisions = cls.temp_dir / "human_decisions.json"
        os.environ["CAMPUS_CUSTOMS_DB_PATH"] = str(cls.test_database)
        os.environ["CAMPUS_CUSTOMS_AUDIT_PATH"] = str(cls.test_audit)
        os.environ["CAMPUS_CUSTOMS_DECISIONS_PATH"] = str(cls.test_decisions)

        from backend import audit, main

        cls.audit_module = audit
        cls.main = main
        cls.client_class = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient
        cls.audit_module.AUDIT_PATH = cls.test_audit

    @classmethod
    def tearDownClass(cls) -> None:
        os.environ.pop("CAMPUS_CUSTOMS_DB_PATH", None)
        os.environ.pop("CAMPUS_CUSTOMS_AUDIT_PATH", None)
        os.environ.pop("CAMPUS_CUSTOMS_DECISIONS_PATH", None)
        os.environ.pop("CAMPUS_CUSTOMS_WORKFLOW_ID", None)
        cls.temp_root.cleanup()

    def setUp(self) -> None:
        original = PROJECT_ROOT / "data" / "campus_customs.db"
        shutil.copy2(original, self.test_database)
        self.test_audit.write_text("[]\n", encoding="utf-8")
        self.test_decisions.write_text("[]\n", encoding="utf-8")
        self.main._runs.clear()
        self.main._run_tasks.clear()
        self.client = self.client_class(self.main.app)

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("CAMPUS_CUSTOMS_WORKFLOW_ID", None)

    @staticmethod
    def _mcp(tool: str, arguments: dict, *, approval_server: bool = False):
        from backend.mcp_client import call_mcp_tool

        return asyncio.run(call_mcp_tool(tool, arguments, approval_server=approval_server))

    def _pay_invoice_501(self) -> dict:
        os.environ["CAMPUS_CUSTOMS_WORKFLOW_ID"] = "workflow-test-101"
        proposal = self._mcp("request_payment_approval", {
            "ticket_id": 101, "kind": "invoice", "ref_id": 501,
            "amount": 840, "account": "checking",
        })
        payment = self._mcp("approve_and_process", {
            "request_type": "payment", "request_id": proposal["request_id"],
            "approved_by": "Disposable test reviewer",
            "confirm_vendor_can_supply": False,
        }, approval_server=True)
        return {"proposal": proposal, "payment": payment}

    def _record_completed_workflow(self) -> None:
        workflow_id = os.environ.get("CAMPUS_CUSTOMS_WORKFLOW_ID", "workflow-test-101")
        self.audit_module.append_audit_record({
            "event": "workflow_started", "workflow_id": workflow_id, "ticket_id": 101,
        })
        self.audit_module.append_audit_record({
            "event": "workflow_completed", "workflow_id": workflow_id,
            "ticket_id": 101, "status": "needs_human",
        })

    def test_payment_request_kind_is_typed_and_limited(self) -> None:
        from mcp_server import server

        self.assertEqual(
            get_type_hints(server.request_payment_approval)["kind"],
            Literal["invoice", "rent"],
        )

    def test_processed_payment_cannot_be_requested_or_processed_twice(self) -> None:
        self._pay_invoice_501()
        with self.assertRaises(Exception):
            self._mcp("request_payment_approval", {
                "ticket_id": 101, "kind": "invoice", "ref_id": 501,
                "amount": 840, "account": "checking",
            })
        response = self.client.post(
            "/api/approvals/payment/1/approve",
            json={"approved_by": "Disposable test reviewer", "confirm": True},
        )
        self.assertEqual(response.status_code, 409)
        context = self._mcp("get_ticket_financial_context", {"ticket_id": 101})
        self.assertEqual(context["cash_accounts"][0]["balance"], 2560)
        self.assertEqual(len(context["recorded_payments"]), 1)
        self.assertEqual(context["recorded_payments"][0]["id"], 1)

    def test_pending_payment_proposal_cannot_be_duplicated(self) -> None:
        proposal = self._mcp("request_payment_approval", {
            "ticket_id": 101, "kind": "invoice", "ref_id": 501,
            "amount": 840, "account": "checking",
        })
        with self.assertRaises(Exception) as caught:
            self._mcp("request_payment_approval", {
                "ticket_id": 101, "kind": "invoice", "ref_id": 501,
                "amount": 840, "account": "checking",
            })
        self.assertIn("duplicate requests are not allowed", str(caught.exception))
        context = self._mcp("get_ticket_financial_context", {"ticket_id": 101})
        self.assertEqual(len(context["ticket_payment_requests"]), 1)
        self.assertEqual(context["ticket_payment_requests"][0]["id"], proposal["request_id"])
        self.assertEqual(context["cash_accounts"][0]["balance"], 3400)

    def test_queued_run_prevents_duplicate_ticket_run(self) -> None:
        self.main._runs["queued-test"] = {
            "run_id": "queued-test", "ticket_id": 101, "status": "queued",
        }
        response = self.client.post("/api/tickets/101/run")
        self.assertEqual(response.status_code, 409)
        self.assertIn("already active", response.json()["detail"])
        continuation = self.client.get("/api/tickets/101/continuation").json()
        self.assertFalse(continuation["eligible"])
        self.assertIn("already active", continuation["reason"])

    def test_paid_invoice_alone_cannot_resolve_out_of_stock_order(self) -> None:
        self._pay_invoice_501()
        self._record_completed_workflow()
        result = self._mcp("mark_ticket_resolved", {
            "ticket_id": 101,
            "resolution_note": "Attempted test resolution after invoice payment only.",
        })
        self.assertEqual(result["status"], "open")
        self.assertTrue(any("fulfillment decision" in item for item in result["outstanding"]))
        refusal_events = [
            row for row in self.audit_module.read_audit_records(limit=200)
            if row.get("event") == "mcp_tool"
            and row.get("tool") == "mark_ticket_resolved"
        ]
        self.assertEqual(len(refusal_events), 1)
        self.assertEqual(refusal_events[0]["outcome"], "success")
        blocked_events = [row for row in self.audit_module.read_audit_records(limit=200)
                          if row.get("event") == "final_disposition_blocked"]
        self.assertTrue(blocked_events)
        tickets = self._mcp("list_tickets", {})
        ticket = next(row for row in tickets if row["id"] == 101)
        self.assertEqual(ticket["status"], "open")
        restock = self._mcp("get_ticket_restock_options", {"ticket_id": 101})
        self.assertEqual(restock["ticket_linked_invoice"]["id"], 501)
        self.assertEqual(restock["ticket_linked_invoice"]["vendor_id"], 1)
        self.assertEqual(restock["ticket_linked_invoice"]["description"], "Rush reprint CC-TEE-WHITE S")
        self.assertEqual(restock["ticket_linked_invoice"]["status"], "paid")
        context = self._mcp("get_ticket_financial_context", {"ticket_id": 101})
        self.assertEqual(context["linked_invoice"]["status"], "paid")
        self.assertEqual(context["cash_accounts"][0]["balance"], 2560)

    def test_order_resolution_uses_human_disposition_without_stock_change(self) -> None:
        self._pay_invoice_501()
        self._record_completed_workflow()
        pending = self._mcp("request_human_decision", {
            "ticket_id": 101, "kind": "fulfillment",
            "question": "How should the unavailable size-S tee be handled?",
            "recommendation": "Defer until a human confirms restock; promise no delivery date.",
            "options": ["Defer", "Backorder", "Cancel"],
        })
        self._mcp("record_human_decision", {
            "decision_id": pending["decision_id"],
            "decided_by": "Disposable test reviewer", "choice": "Defer",
            "terms": "No delivery date promised; customer message remains a draft.",
        }, approval_server=True)
        first = self._mcp("mark_ticket_resolved", {
            "ticket_id": 101, "resolution_note": "Human selected a deferred fulfillment disposition."
        })
        second = self._mcp("mark_ticket_resolved", {
            "ticket_id": 101, "resolution_note": "Repeat call must remain idempotent."
        })
        self.assertEqual(first["status"], "resolved")
        self.assertFalse(first["inventory_consumed"])
        self.assertTrue(second["already_resolved"])
        product = self._mcp("get_ticket_product_context", {"ticket_id": 101})
        self.assertEqual(product["available_qty"], 0)
        tickets = self._mcp("list_tickets", {})
        self.assertEqual(next(row for row in tickets if row["id"] == 101)["status"], "resolved")
        resolution_events = [
            row for row in self.audit_module.read_audit_records(limit=200)
            if row.get("event") == "mcp_tool"
            and row.get("tool") == "mark_ticket_resolved"
        ]
        self.assertEqual([row["outcome"] for row in resolution_events], ["success", "success"])

    def test_dashboard_records_human_decision_and_rechecks_final_disposition(self) -> None:
        self._pay_invoice_501()
        self._record_completed_workflow()
        pending = self._mcp("request_human_decision", {
            "ticket_id": 101, "kind": "fulfillment",
            "question": "How should the unavailable size-S tee be handled?",
            "recommendation": "Defer until a human confirms restock; promise no delivery date.",
            "options": ["Defer", "Backorder", "Cancel"],
        })
        queue = self.client.get("/api/decisions?status=pending")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.json()["count"], 1)
        response = self.client.post(f"/api/decisions/{pending['decision_id']}", json={
            "decided_by": "Dashboard reviewer", "choice": "Defer",
            "terms": "No delivery date promised; customer message remains a draft.",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["decision"]["decided_by"], "Dashboard reviewer")
        self.assertEqual(response.json()["final_disposition"]["status"], "resolved")
        tickets = self._mcp("list_tickets", {})
        self.assertEqual(next(row for row in tickets if row["id"] == 101)["status"], "resolved")
        product = self._mcp("get_ticket_product_context", {"ticket_id": 101})
        self.assertEqual(product["available_qty"], 0)

    def test_human_payment_route_returns_processed_result_and_keeps_stockout_open(self) -> None:
        os.environ["CAMPUS_CUSTOMS_WORKFLOW_ID"] = "workflow-test-101"
        self._mcp("request_payment_approval", {
            "ticket_id": 101, "kind": "invoice", "ref_id": 501,
            "amount": 840, "account": "checking",
        })
        self._record_completed_workflow()
        response = self.client.post("/api/approvals/payment/1/approve", json={
            "approved_by": "Dashboard reviewer", "confirm": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], 101)
        self.assertEqual(payload["new_balance"], 2560.0)
        self.assertEqual(payload["final_disposition"]["status"], "open")
        self.assertTrue(any("fulfillment decision" in item
                            for item in payload["final_disposition"]["outstanding"]))

    def test_price_override_requires_fact_matched_note_and_saved_draft(self) -> None:
        result = self._mcp("mark_ticket_resolved", {
            "ticket_id": 103,
            "resolution_note": "Attempted test resolution without pricing/draft evidence.",
        })
        self.assertEqual(result["status"], "open")
        self.assertTrue(any("fact-matched" in item for item in result["outstanding"]))
        tickets = self._mcp("list_tickets", {})
        self.assertEqual(next(row for row in tickets if row["id"] == 103)["status"], "open")

    def test_active_agent_cannot_change_ticket_status_through_mcp(self) -> None:
        workflow_id = "active-agent-status-guard-test"
        self.audit_module.append_audit_record({
            "event": "agent_run_started", "workflow_id": workflow_id,
            "ticket_id": 103, "agent": "boss", "call_number": 1,
        })
        os.environ["CAMPUS_CUSTOMS_WORKFLOW_ID"] = workflow_id
        with self.assertRaises(Exception):
            self._mcp("mark_ticket_resolved", {
                "ticket_id": 103, "resolution_note": "An agent must not set status early."
            })
        tickets = self._mcp("list_tickets", {})
        self.assertEqual(next(row for row in tickets if row["id"] == 103)["status"], "open")

    def test_continuation_requires_processed_payment_and_needs_human_run(self) -> None:
        self._pay_invoice_501()
        self.main.append_audit_record({
            "event": "api_ticket_run_finished",
            "workflow_id": "workflow-test-101",
            "ticket_id": 101,
            "status": "needs_human",
        })
        response = self.client.get("/api/tickets/101/continuation")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["eligible"])
        self.assertEqual(payload["processed_payments"][0]["amount"], 840)

        invoked = []

        async def fake_background_run(
            run_id: str, ticket_id: int, *,
            continuation_after_payment: bool = False,
            continuation_of: str | None = None,
        ) -> None:
            invoked.append((ticket_id, continuation_after_payment, continuation_of))
            self.main._runs[run_id]["status"] = "test_stubbed"
            self.main._run_tasks.pop(run_id, None)

        with patch.object(self.main, "_run_ticket_in_background", fake_background_run):
            queued = self.client.post("/api/tickets/101/continue")
            self.assertEqual(queued.status_code, 202)
            time.sleep(0.05)
        self.assertEqual(invoked, [(101, True, "workflow-test-101")])
        self.assertTrue(queued.json()["continuation_after_payment"])

    def test_failed_agent_run_surfaces_error_and_releases_ticket_lock(self) -> None:
        async def fail_with_request_limit(*_args, **_kwargs):
            raise UsageLimitExceeded("Exceeded the request_limit of 4")

        with patch.object(self.main, "resolve_ticket", fail_with_request_limit):
            queued = self.client.post("/api/tickets/101/run")
            self.assertEqual(queued.status_code, 202)
            run_id = queued.json()["run_id"]
            time.sleep(0.05)

        run = self.client.get(f"/api/runs/{run_id}").json()
        self.assertEqual(run["status"], "failed")
        self.assertIn("UsageLimitExceeded", run["error"])
        self.assertFalse(self.main._ticket_has_active_run(101))
        events = self.audit_module.read_audit_records(limit=100)
        self.assertTrue(any(
            item.get("event") == "api_ticket_run_failed"
            and item.get("workflow_id") == run_id
            for item in events
        ))

    def test_portkey_model_remains_required_model_with_supported_setting(self) -> None:
        with patch.dict(os.environ, {"PORTKEY_API_KEY": "test-key-no-network-request"}):
            from backend.agents import REQUIRED_MODEL, create_portkey_model

            model = create_portkey_model()
        self.assertEqual(model.model_name, "gpt-6-luna")
        self.assertEqual(model.settings["openai_reasoning_effort"], "none")
        self.assertEqual(REQUIRED_MODEL, "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
