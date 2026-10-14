"""Regression tests for human decisions and final-disposition gates on disposable data."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from inspect import signature


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server"))
import server  # noqa: E402
import approval_server  # noqa: E402


class ResolutionDispositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="campus-customs-disposition-")
        self.temp_path = Path(self.temp.name)
        self.database = self.temp_path / "working-copy.db"
        shutil.copy2(ROOT / "data" / "campus_customs.db", self.database)
        self.previous_paths = (server.DATABASE_PATH, server.AUDIT_PATH, server.DECISIONS_PATH)
        server.DATABASE_PATH = self.database
        server.AUDIT_PATH = self.temp_path / "audit.json"
        server.DECISIONS_PATH = self.temp_path / "decisions.json"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        server.DATABASE_PATH, server.AUDIT_PATH, server.DECISIONS_PATH = self.previous_paths
        self.temp.cleanup()

    def completed_workflow(self) -> None:
        server._append_audit_record({
            "event": "workflow_started", "workflow_id": "test-ticket-101",
            "ticket_id": 101,
        })
        server._append_audit_record({
            "event": "workflow_completed", "workflow_id": "test-ticket-101",
            "ticket_id": 101, "status": "needs_human",
        })

    def test_invoice_payment_alone_does_not_resolve_but_fulfillment_decision_does(self) -> None:
        proposal = server.request_payment_approval(
            ticket_id=101, kind="invoice", ref_id=501, amount=840, account="checking"
        )
        payment = approval_server.approve_and_process(
            request_type="payment", request_id=proposal["request_id"],
            approved_by="Regression Test Human",
        )
        self.assertEqual(payment["new_balance"], 2560.0)

        server._append_audit_record({
            "event": "api_ticket_run_requested", "workflow_id": "test-ticket-101",
            "ticket_id": 101,
        })

        blocked_before_run_end = server.mark_ticket_resolved(
            ticket_id=101, resolution_note="Testing workflow-completion gate"
        )
        self.assertEqual(blocked_before_run_end["status"], "open")
        self.assertTrue(any("queued" in item for item in blocked_before_run_end["outstanding"]))

        self.completed_workflow()
        blocked_after_payment = server.mark_ticket_resolved(
            ticket_id=101, resolution_note="Invoice paid; stockout still needs a human disposition"
        )
        self.assertEqual(blocked_after_payment["status"], "open")
        self.assertTrue(any("fulfillment decision" in item for item in blocked_after_payment["outstanding"]))
        self.assertEqual(server.get_cash_balance("checking")["balance"], 2560.0)

        pending = server.request_human_decision(
            ticket_id=101,
            kind="fulfillment",
            question="How should the shop handle the unavailable size-S tee?",
            recommendation=(
                "Defer until a human confirms Bulldog Print Co. can restock the exact tee; "
                "keep any customer message as a draft and promise no delivery date."
            ),
            options=["Defer until restock is confirmed", "Backorder", "Cancel the order"],
        )
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(server.list_tickets()[0]["status"], "open")
        pending_retry = server.request_human_decision(
            ticket_id=101, kind="fulfillment", question="Duplicate?",
            recommendation="No", options=["Yes", "No"],
        )
        self.assertEqual(pending_retry["decision_id"], pending["decision_id"])
        self.assertTrue(pending_retry["reused_existing"])
        self.assertEqual(len(server.list_decisions(101)), 1)
        pending_disposition = server.mark_ticket_resolved(
            ticket_id=101, resolution_note="The human choice is still pending"
        )
        self.assertEqual(pending_disposition["status"], "open")
        self.assertTrue(any(pending["decision_id"] in item
                            for item in pending_disposition["outstanding"]))

        human_result = approval_server.record_human_decision(
            decision_id=pending["decision_id"],
            decided_by="Regression Test Human",
            choice="Defer until restock is confirmed",
            terms="No delivery date promised; contact remains a draft pending review.",
        )
        decided_store = server.DECISIONS_PATH.read_bytes()
        decided_retry = server.request_human_decision(
            ticket_id=101,
            kind="fulfillment",
            question="Retry must reuse the human's existing disposition.",
            recommendation="Ask the human again?",
            options=["Change the decision", "Keep it"],
        )
        self.assertEqual(decided_retry["decision_id"], pending["decision_id"])
        self.assertTrue(decided_retry["reused_existing"])
        self.assertEqual(decided_retry["status"], "decided")
        self.assertEqual(decided_retry["choice"], "Defer until restock is confirmed")
        self.assertEqual(decided_retry["terms"], human_result["terms"])
        self.assertEqual(server.DECISIONS_PATH.read_bytes(), decided_store)
        self.assertEqual(len(server.list_decisions(101)), 1)
        timestamp = datetime.fromisoformat(human_result["decided_at"])
        self.assertIsNotNone(timestamp.tzinfo)
        self.assertLessEqual(timestamp.astimezone(timezone.utc), datetime.now(timezone.utc))
        self.assertEqual(server.list_tickets()[0]["status"], "open")
        self.assertEqual(set(signature(approval_server.record_human_decision).parameters),
                         {"decision_id", "decided_by", "choice", "terms"})
        with self.assertRaises(TypeError):
            approval_server.record_human_decision(
                decision_id=pending["decision_id"], decided_by="Later",
                choice="Backorder", terms="No date", decided_at="2020-01-01T00:00:00Z",
            )
        with self.assertRaisesRegex(ValueError, "already been decided"):
            approval_server.record_human_decision(
                decision_id=pending["decision_id"], decided_by="Second Human",
                choice="Backorder", terms="No date",
            )
        with self.assertRaisesRegex(ValueError, "not found"):
            approval_server.record_human_decision(
                decision_id="unknown-decision-id", decided_by="Second Human",
                choice="Backorder", terms="No date",
            )
        with self.assertRaisesRegex(ValueError, "decided_by"):
            approval_server.record_human_decision(
                decision_id="unknown", decided_by=" ", choice="Backorder", terms="No date"
            )

        resolved = server.mark_ticket_resolved(
            ticket_id=101, resolution_note="The out-of-stock fulfillment outcome was decided by a human"
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertFalse(resolved["inventory_consumed"])
        self.assertEqual(resolved["evidence"]["inventory_check"]["available_qty"], 0)
        self.assertEqual(server.list_tickets()[0]["status"], "resolved")
        product = server.get_ticket_product_context(101)
        self.assertEqual(product["available_qty"], 0)
        audit = json.loads(server.AUDIT_PATH.read_text(encoding="utf-8"))
        human_event = next(row for row in audit if row.get("event") == "human_decision")
        self.assertEqual(human_event["question"], "How should the shop handle the unavailable size-S tee?")
        self.assertEqual(human_event["choice"], "Defer until restock is confirmed")
        self.assertTrue(human_event["terms"])
        self.assertEqual(human_event["decided_by"], "Regression Test Human")
        disposition = next(row for row in reversed(audit) if row.get("event") == "final_disposition")
        self.assertEqual(disposition["evidence"]["fulfillment_decision"]["decided_by"], "Regression Test Human")

    def test_privileged_decision_tool_is_not_exposed_to_agents(self) -> None:
        agent_tools = set(server.mcp._tool_manager._tools)
        human_tools = set(approval_server.approval_mcp._tool_manager._tools)
        self.assertNotIn("record_human_decision", agent_tools)
        self.assertIn("record_human_decision", human_tools)
        self.assertTrue(
            server.mcp._tool_manager._tools["request_human_decision"].annotations.idempotentHint
        )

    def test_ticket_103_retry_reuses_decided_discount_and_rejects_fulfillment_kind(self) -> None:
        pending = server.request_human_decision(
            ticket_id=103,
            kind="discount",
            question="What target price should be assessed?",
            recommendation="Ask the customer for a target before proposing a discount.",
            options=["Ask for target unit price", "Retain list price"],
        )
        decided = approval_server.record_human_decision(
            decision_id=pending["decision_id"],
            decided_by="Offline test reviewer",
            choice="Ask for target unit price",
            terms="Keep any customer wording as an unsent draft.",
        )
        store_before_retry = server.DECISIONS_PATH.read_bytes()

        reused = server.request_human_decision(
            ticket_id=103,
            kind="discount",
            question="A retry asks the same decision with different generated wording.",
            recommendation="Do not overwrite the human's recorded choice.",
            options=["New option A", "New option B"],
        )
        self.assertEqual(reused["decision_id"], decided["decision_id"])
        self.assertEqual(reused["status"], "decided")
        self.assertEqual(reused["choice"], decided["choice"])
        self.assertEqual(reused["terms"], decided["terms"])
        self.assertTrue(reused["reused_existing"])
        self.assertEqual(len(server.list_decisions(ticket_id=103)), 1)
        self.assertEqual(server.DECISIONS_PATH.read_bytes(), store_before_retry)

        with self.assertRaisesRegex(ValueError, "only valid for customer orders"):
            server.request_human_decision(
                ticket_id=103,
                kind="fulfillment",
                question="Do not classify this price override as fulfillment.",
                recommendation="Use a discount disposition instead.",
                options=["Defer", "Cancel"],
            )


if __name__ == "__main__":
    unittest.main()
