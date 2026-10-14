"""Offline tests for Ticket 101's evidence-gated Boss-only recovery path."""

from __future__ import annotations

import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, Mock, patch

from pydantic_ai.models.test import TestModel

from backend.models import AgentReport
from backend.recovery import (
    BossSynthesisCandidate,
    REQUIRED_SAVED_SPECIALISTS,
    RecoveryNotEligible,
    find_boss_synthesis_candidate,
    resume_failed_boss_synthesis,
)


SOURCE_WORKFLOW = "failed-boss-token-limit-101"
RECOVERY_WORKFLOW = "offline-boss-recovery-101"
DECISION_ID = "decision-101-offline"


def report(role: str) -> AgentReport:
    return AgentReport(
        status="needs_human" if role in {"inventory", "accounting"} else "complete",
        summary=f"Persisted {role} findings from the failed source workflow.",
        findings=[f"Saved {role} finding: Ticket 101 needs one CC-TEE-WHITE size S."],
        decisions=[f"Saved {role} decision: do not claim delivery."],
        next_steps=[f"Saved {role} next step: defer fulfillment honestly."],
        human_approval_required=role != "customer_service",
    )


def source_history() -> list[dict]:
    rows = [{
        "event": "workflow_started", "workflow": "resolve_ticket",
        "workflow_id": SOURCE_WORKFLOW, "ticket_id": 101,
        "timestamp": "2026-10-12T20:00:00+00:00",
    }]
    for role in REQUIRED_SAVED_SPECIALISTS:
        rows.append({
            "event": "agent_run_completed", "workflow_id": SOURCE_WORKFLOW,
            "ticket_id": 101, "agent": role, "parent_agent": "boss",
            "delegation_depth": 1, "output": report(role).model_dump(mode="json"),
        })
    rows.extend([
        {
            "event": "agent_run_failed", "workflow_id": SOURCE_WORKFLOW,
            "ticket_id": 101, "agent": "boss",
            "error": "UsageLimitExceeded: Exceeded the total_tokens_limit of 12000 (total_tokens=14308)",
        },
        {
            "event": "workflow_failed", "workflow": "resolve_ticket",
            "workflow_id": SOURCE_WORKFLOW, "ticket_id": 101,
        },
        {
            "event": "api_ticket_run_failed", "workflow_id": SOURCE_WORKFLOW,
            "ticket_id": 101,
        },
    ])
    return rows


def current_mcp_facts() -> dict[str, dict | list]:
    options = ["Defer pending human-confirmed restock (no delivery date promised)"]
    return {
        "list_tickets": [{
            "id": 101, "type": "customer_order", "subject": "Bulldog tee",
            "invoice_id": 501, "sku": "CC-TEE-WHITE", "size": "S", "qty": 1,
            "status": "open",
        }],
        "get_ticket_financial_context": {
            "linked_invoice": {"id": 501, "status": "paid", "amount": 840.0},
            "ticket_payment_requests": [{
                "id": 1, "kind": "invoice", "ref_id": 501,
                "amount": 840.0, "status": "processed",
            }],
            "ticket_purchase_requests": [],
            "recorded_payments": [{
                "id": 1, "kind": "invoice", "ref_id": 501,
                "amount": 840.0, "account": "checking", "approved_by": "Human tester",
            }],
            "cash_accounts": [{"name": "checking", "balance": 2560.0}],
        },
        "list_decisions": [{
            "decision_id": DECISION_ID, "ticket_id": 101, "kind": "fulfillment",
            "status": "decided", "decided_by": "Human tester",
            "choice": options[0], "options": options,
            "terms": "Defer pending exact-item confirmation; no delivery date promised.",
        }],
        "get_ticket_product_context": {
            "ticket_id": 101, "sku": "CC-TEE-WHITE", "size": "S",
            "requested_qty": 1, "available_qty": 0, "shortfall_qty": 1,
            "stock_status": "out_of_stock",
        },
    }


def complete_boss_output() -> dict:
    return AgentReport(
        status="complete",
        summary=("Boss reviewed the saved specialist results and current MCP facts. "
                 "The human chose to defer fulfillment pending exact-item confirmation; "
                 "no delivery date is promised, and no item is recorded as received."),
        findings=["Invoice 501 is paid once; checking remains $2,560."],
        decisions=["Honor the recorded human deferral; do not claim delivery."],
        next_steps=["Wait for confirmed stock before promising fulfillment."],
        human_approval_required=False,
    ).model_dump(mode="json")


class SynthesisRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_boss_reuses_saved_reports_with_offline_test_model_only(self) -> None:
        facts = current_mcp_facts()
        called_tools: list[str] = []
        with tempfile.TemporaryDirectory(prefix="campus-customs-recovery-test-") as tmp:
            root = Path(tmp)
            audit_fixture = root / "audit.json"
            mcp_fixture = root / "mcp_read_facts.json"
            audit_fixture.write_text(json.dumps(source_history()), encoding="utf-8")
            mcp_fixture.write_text(json.dumps(facts), encoding="utf-8")
            saved_history = json.loads(audit_fixture.read_text(encoding="utf-8"))

            async def mock_mcp(tool: str, _arguments: dict, *, workflow_id: str):
                self.assertEqual(workflow_id, RECOVERY_WORKFLOW)
                called_tools.append(tool)
                saved_mcp_facts = json.loads(mcp_fixture.read_text(encoding="utf-8"))
                return saved_mcp_facts[tool]

            appended: list[dict] = []
            model = TestModel(custom_output_args=complete_boss_output())
            with patch("backend.recovery.append_audit_record", side_effect=appended.append):
                result = await resume_failed_boss_synthesis(
                    101,
                    SOURCE_WORKFLOW,
                    RECOVERY_WORKFLOW,
                    model=model,
                    tool_caller=mock_mcp,
                    history=saved_history,
                )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.agent_calls, 1)
        self.assertEqual(result.boss_report.status, "complete")
        self.assertCountEqual(called_tools, [
            "list_tickets", "get_ticket_financial_context", "list_decisions",
            "get_ticket_product_context",
        ])
        self.assertEqual(model.last_model_request_parameters.function_tools, [])
        completed = next(row for row in appended if row["event"] == "workflow_completed")
        self.assertEqual(completed["saved_specialists_reused"], list(REQUIRED_SAVED_SPECIALISTS))
        preflight = next(row for row in appended if row["event"] == "boss_synthesis_recovery_preflight")
        self.assertEqual(preflight["current_evidence"]["financial"]["checking_balance"], 2560.0)

    async def test_missing_or_corrupt_saved_specialist_evidence_is_rejected(self) -> None:
        missing = source_history()
        missing = [row for row in missing if not (
            row.get("event") == "agent_run_completed" and row.get("agent") == "accounting"
        )]
        duplicate = source_history()
        duplicate.append(next(row.copy() for row in duplicate
                             if row.get("event") == "agent_run_completed"
                             and row.get("agent") == "inventory"))
        corrupt = source_history()
        next(row for row in corrupt if row.get("agent") == "customer_service")["output"] = {"status": "broken"}

        for rows in (missing, duplicate, corrupt):
            with self.subTest(rows=rows):
                with self.assertRaises(RecoveryNotEligible):
                    find_boss_synthesis_candidate(101, SOURCE_WORKFLOW, history=rows)

    async def test_stale_payment_or_decision_prerequisite_rejects_before_model_creation(self) -> None:
        cases = ("payment", "decision")
        for missing in cases:
            facts = current_mcp_facts()
            if missing == "payment":
                facts["get_ticket_financial_context"]["cash_accounts"][0]["balance"] = 2500.0
            else:
                facts["list_decisions"][0]["status"] = "pending"

            async def mock_mcp(tool: str, _arguments: dict, *, workflow_id: str):
                return facts[tool]

            with self.subTest(missing=missing):
                with patch("backend.recovery.Agent", side_effect=AssertionError("model must not be created")):
                    with self.assertRaises(RecoveryNotEligible):
                        await resume_failed_boss_synthesis(
                            101, SOURCE_WORKFLOW, RECOVERY_WORKFLOW,
                            model=TestModel(custom_output_args=complete_boss_output()),
                            tool_caller=mock_mcp,
                            history=source_history(),
                        )

    async def test_boss_cannot_claim_delivery_in_a_complete_recovery(self) -> None:
        facts = current_mcp_facts()

        async def mock_mcp(tool: str, _arguments: dict, *, workflow_id: str):
            return facts[tool]

        output = complete_boss_output()
        output["summary"] = "The tee has been delivered to the customer."
        appended: list[dict] = []
        with patch("backend.recovery.append_audit_record", side_effect=appended.append):
            with self.assertRaisesRegex(RecoveryNotEligible, "claiming receipt or delivery"):
                await resume_failed_boss_synthesis(
                    101,
                    SOURCE_WORKFLOW,
                    RECOVERY_WORKFLOW,
                    model=TestModel(custom_output_args=output),
                    tool_caller=mock_mcp,
                    history=source_history(),
                )
        self.assertFalse(any(row["event"] == "workflow_completed" for row in appended))

    async def test_non_token_failure_and_non_101_workflow_are_not_recoverable(self) -> None:
        wrong_error = source_history()
        next(row for row in wrong_error if row.get("agent") == "boss")["error"] = "network error"
        with self.assertRaises(RecoveryNotEligible):
            find_boss_synthesis_candidate(101, SOURCE_WORKFLOW, history=wrong_error)
        with self.assertRaises(RecoveryNotEligible):
            find_boss_synthesis_candidate(102, SOURCE_WORKFLOW, history=source_history())

    async def test_queued_recovery_cannot_be_submitted_twice_even_after_run_state_reload(self) -> None:
        history = source_history()
        history.append({
            "event": "api_ticket_run_requested", "workflow_id": RECOVERY_WORKFLOW,
            "ticket_id": 101, "synthesis_recovery": True,
            "recovery_of": SOURCE_WORKFLOW,
        })
        with self.assertRaisesRegex(RecoveryNotEligible, "already queued or running"):
            find_boss_synthesis_candidate(101, SOURCE_WORKFLOW, history=history)

        # The active worker itself can still verify its source evidence by
        # presenting its own recovery workflow ID.
        candidate = find_boss_synthesis_candidate(
            101, SOURCE_WORKFLOW, history=history,
            allow_recovery_workflow_id=RECOVERY_WORKFLOW,
        )
        self.assertEqual(candidate.workflow_id, SOURCE_WORKFLOW)


class SynthesisRecoveryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from backend import main

        cls.main = main
        cls.test_client_class = TestClient

    def setUp(self) -> None:
        self.client = self.test_client_class(self.main.app)
        self.main._runs.clear()
        self.main._run_tasks.clear()

    def tearDown(self) -> None:
        self.client.close()

    def test_explicit_recovery_endpoint_requires_candidate_and_live_preflight(self) -> None:
        candidate = find_boss_synthesis_candidate(
            101, SOURCE_WORKFLOW, history=source_history()
        )
        queue = Mock(side_effect=lambda ticket_id, failed_id, *, run_id: {
            "run_id": run_id, "ticket_id": ticket_id, "status": "queued",
            "synthesis_recovery": True, "recovery_of": failed_id,
        })
        with (
            patch.object(self.main, "find_boss_synthesis_candidate", return_value=candidate),
            patch.object(self.main, "load_verified_current_evidence", new_callable=AsyncMock),
            patch.object(self.main, "_ticket_has_active_run", return_value=False),
            patch.object(self.main, "_queue_boss_synthesis_recovery", queue),
            patch.object(self.main, "append_audit_record"),
        ):
            response = self.client.post(
                "/api/tickets/101/recover-synthesis",
                json={"failed_workflow_id": SOURCE_WORKFLOW},
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.json()["synthesis_recovery"])
        queue.assert_called_once()

    def test_ticket_103_recovery_endpoint_queues_only_boss_synthesis(self) -> None:
        candidate = BossSynthesisCandidate(
            ticket_id=103,
            workflow_id="failed-ticket-103-source",
            workflow_started_at="2026-10-14T01:51:55+00:00",
            reports={
                "accounting": report("accounting"),
                "customer_service": report("customer_service"),
            },
            board_write_ids={"accounting": 2, "customer_service": 3},
        )
        queue = Mock(side_effect=lambda ticket_id, failed_id, *, run_id: {
            "run_id": run_id, "ticket_id": ticket_id, "status": "queued",
            "synthesis_recovery": True, "recovery_of": failed_id,
        })
        with (
            patch.object(self.main, "find_boss_synthesis_candidate", return_value=candidate),
            patch.object(self.main, "load_verified_current_evidence", new_callable=AsyncMock),
            patch.object(self.main, "_ticket_has_active_run", return_value=False),
            patch.object(self.main, "_queue_boss_synthesis_recovery", queue),
            patch.object(self.main, "append_audit_record"),
        ):
            response = self.client.post(
                "/api/tickets/103/recover-synthesis",
                json={"failed_workflow_id": candidate.workflow_id},
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["recovery_of"], candidate.workflow_id)
        queue.assert_called_once()

    def test_recovery_endpoint_does_not_queue_when_preflight_fails(self) -> None:
        candidate = find_boss_synthesis_candidate(
            101, SOURCE_WORKFLOW, history=source_history()
        )
        queue = Mock()
        with (
            patch.object(self.main, "find_boss_synthesis_candidate", return_value=candidate),
            patch.object(
                self.main, "load_verified_current_evidence", new_callable=AsyncMock,
                side_effect=RecoveryNotEligible("Checking balance changed."),
            ),
            patch.object(self.main, "_ticket_has_active_run", return_value=False),
            patch.object(self.main, "_queue_boss_synthesis_recovery", queue),
        ):
            response = self.client.post(
                "/api/tickets/101/recover-synthesis",
                json={"failed_workflow_id": SOURCE_WORKFLOW},
            )

        self.assertEqual(response.status_code, 409)
        queue.assert_not_called()

    def test_ordinary_run_route_refuses_to_repeat_saved_specialist_work(self) -> None:
        queue = Mock()
        with (
            patch.object(self.main, "_shop_tool", new_callable=AsyncMock, return_value=[{
                "id": 101, "status": "open",
            }]),
            patch.object(self.main, "_ticket_has_active_run", return_value=False),
            patch.object(self.main, "recovery_candidate_status", return_value={
                "ticket_id": 101, "eligible": True,
            }),
            patch.object(self.main, "_queue_ticket_run", queue),
        ):
            response = self.client.post("/api/tickets/101/run")

        self.assertEqual(response.status_code, 409)
        self.assertIn("Boss-synthesis recovery", response.json()["detail"])
        queue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
