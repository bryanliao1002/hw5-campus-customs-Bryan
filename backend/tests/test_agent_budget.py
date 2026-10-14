"""Offline regression tests for bounded PydanticAI request loops."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.test import TestModel

from backend.models import AgentReport
from backend.orchestrator import (
    AgentCoordinator,
    MAX_TOKENS_PER_AGENT_CALL,
    MAX_MODEL_REQUESTS_PER_AGENT,
    WorkflowBudget,
    agent_usage_limits,
    resolve_ticket,
)


class AgentBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_budget_allows_a_six_turn_validation_loop_offline(self) -> None:
        attempts = 0
        agent = Agent(
            TestModel(custom_output_text="ready"),
            output_type=str,
            retries=8,
        )

        @agent.output_validator
        def require_sixth_turn(_ctx, output: str) -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 6:
                raise ModelRetry("Continue the bounded local test loop.")
            return output

        result = await agent.run(
            "Exercise a multi-turn tool/validation loop without network access.",
            usage_limits=agent_usage_limits(24_000),
        )

        self.assertEqual(result.output, "ready")
        self.assertEqual(result.usage.requests, 6)
        self.assertEqual(attempts, 6)
        self.assertEqual(MAX_MODEL_REQUESTS_PER_AGENT, 8)
        self.assertEqual(agent_usage_limits(24_000).total_tokens_limit, 14_000)

    async def test_failed_agent_usage_is_audited_and_charged_to_workflow(self) -> None:
        class LimitedAgent:
            async def run(self, _task, **kwargs):
                self.limits = kwargs["usage_limits"]
                usage = kwargs["usage"]
                # Model four completed requests before PydanticAI stopped the
                # invocation at the old request_limit=4 ceiling.
                usage.requests = 4
                usage.input_tokens = 1_300
                usage.output_tokens = 700
                raise UsageLimitExceeded("Exceeded the request_limit of 4")

        fake_agent = LimitedAgent()
        budget = WorkflowBudget()
        coordinator = AgentCoordinator({"boss": fake_agent}, budget)  # type: ignore[arg-type]
        records: list[dict] = []

        with patch("backend.orchestrator.append_audit_record", side_effect=records.append):
            with self.assertRaises(UsageLimitExceeded):
                await coordinator.run_agent(
                    "boss", "Test a bounded failure.", stack=(), ticket_id=101
                )

        self.assertEqual(fake_agent.limits.request_limit, 8)
        self.assertEqual(fake_agent.limits.tool_calls_limit, 12)
        self.assertEqual(fake_agent.limits.output_tokens_limit, 2_400)
        self.assertEqual(budget.tokens_used, 2_000)
        failed = next(record for record in records if record["event"] == "agent_run_failed")
        self.assertEqual(failed["usage"], {
            "requests": 4,
            "tool_calls": 0,
            "total_tokens": 2_000,
        })
        self.assertEqual(failed["workflow_tokens_used"], 2_000)

    async def test_audited_13531_token_boss_synthesis_fits_modest_headroom(self) -> None:
        class BossAgent:
            async def run(self, _task, *, usage, usage_limits, **_kwargs):
                self.limit = usage_limits.total_tokens_limit
                usage.requests = 5
                usage.tool_calls = 3
                usage.input_tokens = 12_000
                usage.output_tokens = 1_531
                return SimpleNamespace(
                    usage=usage,
                    output=AgentReport(status="complete", summary="Synthesis completed."),
                )

        boss = BossAgent()
        budget = WorkflowBudget(max_tokens=24_000)
        coordinator = AgentCoordinator({"boss": boss}, budget)  # type: ignore[arg-type]
        with patch("backend.orchestrator.append_audit_record"):
            result = await coordinator.run_agent(
                "boss", "Complete the bounded synthesis.", stack=(), ticket_id=103
            )

        self.assertEqual(boss.limit, MAX_TOKENS_PER_AGENT_CALL)
        self.assertEqual(MAX_TOKENS_PER_AGENT_CALL, 14_000)
        self.assertEqual(result.summary, "Synthesis completed.")
        self.assertEqual(budget.tokens_used, 13_531)
        self.assertLess(budget.tokens_used, budget.max_tokens)

    async def test_ticket_103_run_plan_avoids_redundant_delegation_and_decisions(self) -> None:
        with patch("backend.orchestrator._run_boss_workflow", new_callable=AsyncMock) as run_workflow:
            run_workflow.return_value = None
            await resolve_ticket(103, workflow_id="offline-ticket-103")

        task = run_workflow.call_args.kwargs["boss_task"]
        self.assertIn("Do not delegate Inventory", task)
        self.assertIn("list_decisions for ticket 103", task)
        self.assertIn("never request kind=fulfillment", task)
        self.assertIn("at most one kind=discount decision", task)
        self.assertFalse(run_workflow.call_args.kwargs["reset_database"])

    async def test_nested_agent_usage_is_charged_once_and_parent_limit_shrinks(self) -> None:
        budget = WorkflowBudget(max_agent_calls=4, max_tokens=5_000)
        coordinator: AgentCoordinator

        class ChildAgent:
            async def run(self, _task, *, usage, usage_limits, **_kwargs):
                self.limits = usage_limits
                usage.input_tokens = 1_200
                return SimpleNamespace(
                    usage=usage,
                    output=AgentReport(status="complete", summary="Saved child result."),
                )

        child = ChildAgent()

        class ParentAgent:
            async def run(self, _task, *, deps, usage, usage_limits, **_kwargs):
                self.limits = usage_limits
                usage.input_tokens = 500
                await coordinator.delegate(
                    parent_role="boss", target_role="inventory",
                    task="Inspect the saved ticket facts.", parent_deps=deps,
                )
                # Parent has spent 500 tokens, child 1,200. Keep 1,200 tokens
                # reserved and enforce the remaining shared budget on resume.
                self.limit_after_child = usage_limits.total_tokens_limit
                self.request_limit_after_child = usage_limits.request_limit
                usage.input_tokens = usage_limits.total_tokens_limit
                return SimpleNamespace(
                    usage=usage,
                    output=AgentReport(status="complete", summary="Synthesized safely."),
                )

        parent = ParentAgent()
        coordinator = AgentCoordinator(
            {"boss": parent, "inventory": child},
            budget,
        )  # type: ignore[arg-type]
        records: list[dict] = []
        with patch("backend.orchestrator.append_audit_record", side_effect=records.append):
            result = await coordinator.run_agent(
                "boss", "Use one bounded peer handoff.", stack=(), ticket_id=101
            )

        self.assertEqual(result.summary, "Synthesized safely.")
        self.assertEqual(parent.limit_after_child, 2_600)
        self.assertEqual(parent.request_limit_after_child, 8)
        self.assertEqual(budget.tokens_used, 3_800)
        self.assertEqual(
            [row["usage"]["total_tokens"] for row in records
             if row["event"] == "agent_run_completed"],
            [1_200, 2_600],
        )


if __name__ == "__main__":
    unittest.main()
