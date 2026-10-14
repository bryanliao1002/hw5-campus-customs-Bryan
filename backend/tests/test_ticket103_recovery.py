"""Ticket 103 recovery tests use copied DB/decision/audit evidence and TestModel only."""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pydantic_ai.models.test import TestModel

from backend import audit
from backend.models import AgentReport
from backend.recovery import RecoveryNotEligible, find_boss_synthesis_candidate, resume_failed_boss_synthesis
from mcp_server import server


ROOT = Path(__file__).resolve().parents[2]
RECOVERY_ID = "offline-ticket-103-boss-recovery"


def boss_output() -> dict:
    return AgentReport(
        status="complete",
        summary=("The human chose to ask for a target unit price; Accounting's analysis is saved, "
                 "and Customer Service's draft is not sent."),
        findings=["Current evidence is 8 of 20 in stock, with 12 short; no offer price was made."],
        decisions=["Honor the decided request for the buyer's target price."],
        next_steps=["Wait for the buyer's target price before evaluating a discount."],
        human_approval_required=False,
    ).model_dump(mode="json")


class Ticket103RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="campus-customs-ticket103-recovery-")
        self.root = Path(self.temp.name)
        self.database = self.root / "campus_customs_new.db"
        self.decisions = self.root / "human_decisions.json"
        self.audit_path = self.root / "audit_trail.json"
        shutil.copy2(ROOT / "data" / "campus_customs_new.db", self.database)
        shutil.copy2(ROOT / "output" / "audit_trail.json", self.audit_path)

        # Recreate the state immediately after the failed synthesis from actual
        # recorded evidence. The committed audit contains the later successful
        # recovery, and a local human_decisions.json is intentionally gitignored.
        all_history = json.loads(self.audit_path.read_text(encoding="utf-8"))
        recovery_request = next(
            (row for row in reversed(all_history)
             if row.get("event") == "api_ticket_run_requested"
             and row.get("ticket_id") == 103
             and row.get("synthesis_recovery") is True
             and isinstance(row.get("recovery_of"), str)),
            None,
        )
        if recovery_request is None:
            raise AssertionError("Recorded Ticket 103 Boss-recovery source is missing from the audit")
        source_workflow_id = recovery_request["recovery_of"]
        source_failure_index = next(
            (index for index in range(len(all_history) - 1, -1, -1)
             if all_history[index].get("event") == "api_ticket_run_failed"
             and all_history[index].get("ticket_id") == 103
             and all_history[index].get("workflow_id") == source_workflow_id),
            None,
        )
        if source_failure_index is None:
            raise AssertionError("Recorded Ticket 103 failed source workflow is missing")
        self.history = all_history[: source_failure_index + 1]
        self.audit_path.write_text(json.dumps(self.history), encoding="utf-8")

        # The append-only audit records the actual human decision in full;
        # project only its persisted decision fields into this disposable test
        # store so the test is self-contained on a clone without the ignored
        # runtime human_decisions.json file.
        decision_records = [
            {key: value for key, value in row.items() if key not in {"event", "timestamp"}}
            for row in all_history
            if row.get("event") == "human_decision"
        ]
        self.decisions.write_text(json.dumps(decision_records), encoding="utf-8")
        self.decision_baseline = self.decisions.read_bytes()

        # The fixture models the eligible pre-recovery point in time. Only the
        # disposable copy is reopened; the submitted working database is never
        # changed by this test.
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE tickets SET status = 'open' WHERE id = 103")
            connection.commit()

        self.old_server_paths = (server.DATABASE_PATH, server.DECISIONS_PATH, server.AUDIT_PATH)
        self.old_audit_path = audit.AUDIT_PATH
        self.old_environment = {
            key: os.environ.get(key)
            for key in ("CAMPUS_CUSTOMS_DB_PATH", "CAMPUS_CUSTOMS_AUDIT_PATH", "CAMPUS_CUSTOMS_DECISIONS_PATH")
        }
        os.environ["CAMPUS_CUSTOMS_DB_PATH"] = str(self.database)
        os.environ["CAMPUS_CUSTOMS_AUDIT_PATH"] = str(self.audit_path)
        os.environ["CAMPUS_CUSTOMS_DECISIONS_PATH"] = str(self.decisions)
        server.DATABASE_PATH = self.database
        server.DECISIONS_PATH = self.decisions
        server.AUDIT_PATH = self.audit_path
        audit.AUDIT_PATH = self.audit_path
        self.addCleanup(self._restore)

        self.candidate = find_boss_synthesis_candidate(103, history=self.history)
        self.assertEqual(set(self.candidate.reports), {"accounting", "customer_service"})
        self.assertEqual(set(self.candidate.board_write_ids or {}), {"accounting", "customer_service"})

    def _restore(self) -> None:
        server.DATABASE_PATH, server.DECISIONS_PATH, server.AUDIT_PATH = self.old_server_paths
        audit.AUDIT_PATH = self.old_audit_path
        for key, value in self.old_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.environ.pop("CAMPUS_CUSTOMS_WORKFLOW_ID", None)
        self.temp.cleanup()

    async def _mcp_copy(self, name: str, arguments: dict, *, workflow_id: str):
        from backend.mcp_client import call_mcp_tool

        return await call_mcp_tool(name, arguments, workflow_id=workflow_id)

    def _db_snapshot(self) -> dict:
        with closing(sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)) as connection:
            return {
                "status": connection.execute("SELECT status FROM tickets WHERE id=103").fetchone()[0],
                "updates": connection.execute(
                    "SELECT id, item_type, author, body FROM ticket_updates WHERE ticket_id=103 ORDER BY id"
                ).fetchall(),
                "payments": connection.execute("SELECT * FROM payments ORDER BY id").fetchall(),
                "cash": connection.execute("SELECT name, balance FROM cash_accounts ORDER BY name").fetchall(),
                "payment_requests": connection.execute(
                    "SELECT * FROM payment_requests WHERE ticket_id=103 ORDER BY id"
                ).fetchall(),
                "purchase_requests": connection.execute(
                    "SELECT * FROM purchase_requests WHERE ticket_id=103 ORDER BY id"
                ).fetchall(),
            }

    async def test_saved_work_recovers_without_repeating_specialists_or_writes(self) -> None:
        before = self._db_snapshot()
        decision_bytes = self.decision_baseline
        appended: list[dict] = []
        model = TestModel(custom_output_args=boss_output())

        def append_recovery_record(record: dict) -> None:
            appended.append(record)
            rows = json.loads(self.audit_path.read_text(encoding="utf-8"))
            rows.append(record)
            self.audit_path.write_text(json.dumps(rows), encoding="utf-8")

        with patch("backend.recovery.append_audit_record", side_effect=append_recovery_record):
            report = await resume_failed_boss_synthesis(
                103,
                self.candidate.workflow_id,
                RECOVERY_ID,
                model=model,
                history=self.history,
                tool_caller=self._mcp_copy,
            )

        self.assertEqual(report.status, "complete")
        self.assertEqual(report.agent_calls, 1)
        self.assertLessEqual(report.tokens_used, 3_000)
        self.assertEqual(model.last_model_request_parameters.function_tools, [])
        self.assertCountEqual(
            [row["tool"] for row in self._audit_tool_calls()],
            ["list_tickets", "get_ticket_financial_context", "list_decisions",
             "get_ticket_product_context", "get_ticket_board_items"],
        )
        self.assertEqual(self._db_snapshot(), before)
        self.assertEqual(self.decisions.read_bytes(), decision_bytes)
        audit_after_recovery = json.loads(self.audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit_after_recovery[:len(self.history)], self.history)
        self.assertTrue(any(row.get("event") == "api_ticket_run_failed"
                            and row.get("workflow_id") == self.candidate.workflow_id
                            for row in audit_after_recovery))
        self.assertEqual([row["event"] for row in appended].count("workflow_completed"), 1)
        recovery_completion = next(row for row in appended if row["event"] == "workflow_completed")
        self.assertEqual(recovery_completion["workflow_id"], RECOVERY_ID)
        self.assertEqual(recovery_completion["recovery_of"], self.candidate.workflow_id)
        self.assertEqual(recovery_completion["saved_specialists_reused"], ["accounting", "customer_service"])
        self.assertTrue(any(row.get("event") == "workflow_failed"
                            and row.get("workflow_id") == self.candidate.workflow_id
                            for row in self.history))

        # Only the separate recovery workflow is complete. The normal, audited MCP
        # final-disposition tool remains the sole status-changing path.
        from backend.mcp_client import call_mcp_tool

        resolution = await call_mcp_tool("mark_ticket_resolved", {
            "ticket_id": 103,
            "resolution_note": "Boss recovered synthesis from saved specialist and current MCP evidence.",
        })
        self.assertEqual(resolution["status"], "resolved")
        self.assertEqual(self._db_snapshot()["status"], "resolved")
        after = self._db_snapshot()
        self.assertEqual(after["updates"], before["updates"])
        self.assertEqual(after["payments"], before["payments"])
        self.assertEqual(after["cash"], before["cash"])
        self.assertEqual(after["payment_requests"], before["payment_requests"])
        self.assertEqual(after["purchase_requests"], before["purchase_requests"])
        self.assertEqual(self.decisions.read_bytes(), decision_bytes)

    async def test_bad_decision_or_board_evidence_fails_before_boss_model(self) -> None:
        model_mock = patch("backend.recovery.Agent", side_effect=AssertionError("LLM must not start"))
        with model_mock:
            pending = json.loads(self.decisions.read_text(encoding="utf-8"))
            decision = next(row for row in pending if row.get("ticket_id") == 103)
            decision["status"] = "pending"
            self.decisions.write_text(json.dumps(pending), encoding="utf-8")
            with self.assertRaisesRegex(RecoveryNotEligible, "single valid human discount decision"):
                await resume_failed_boss_synthesis(
                    103, self.candidate.workflow_id, RECOVERY_ID,
                    model=TestModel(custom_output_args=boss_output()),
                    history=self.history, tool_caller=self._mcp_copy,
                )

        self.decisions.write_bytes(self.decision_baseline)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE ticket_updates SET body = ? WHERE id = ?",
                ("made-up stock/pricing", self.candidate.board_write_ids["accounting"]),
            )
            connection.commit()
        with patch("backend.recovery.Agent", side_effect=AssertionError("LLM must not start")):
            with self.assertRaisesRegex(RecoveryNotEligible, "not valid structured evidence"):
                await resume_failed_boss_synthesis(
                    103, self.candidate.workflow_id, RECOVERY_ID,
                    model=TestModel(custom_output_args=boss_output()),
                    history=self.history, tool_caller=self._mcp_copy,
                )

    def _audit_tool_calls(self) -> list[dict]:
        rows = json.loads(self.audit_path.read_text(encoding="utf-8"))
        return [row for row in rows if row.get("workflow_id") == RECOVERY_ID
                and row.get("event") == "mcp_tool"]


if __name__ == "__main__":
    unittest.main()
