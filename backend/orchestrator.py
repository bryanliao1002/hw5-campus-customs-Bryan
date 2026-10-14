"""Bounded all-to-all agent workflow; every shop-data action goes through MCP."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys
import tempfile

from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage, UsageLimits

from backend.agents import PROJECT_ROOT, build_agents, create_portkey_model
from backend.audit import append_audit_record
from backend.models import AgentDependencies, AgentReport, AgentRole, WorkflowReport


MCP_SERVER_FILE = PROJECT_ROOT / "mcp_server" / "server.py"
MCP_PYTHON = PROJECT_ROOT / "mcp_server" / ".venv" / "bin" / "python"
ORIGINAL_DATABASE = PROJECT_ROOT / "data" / "campus_customs.db"
WORKING_DATABASE = Path(os.environ.get(
    "CAMPUS_CUSTOMS_DB_PATH", str(PROJECT_ROOT / "data" / "campus_customs_new.db")
)).expanduser().resolve()
MAX_AGENT_CALLS = 9
MAX_DELEGATION_DEPTH = 4
MAX_WORKFLOW_TOKENS = 24_000
MAX_TOKENS_PER_AGENT_CALL = 14_000
MAX_OUTPUT_TOKENS_PER_AGENT_CALL = 2_400
MIN_PARENT_RESUME_HEADROOM = 1_200
MAX_MODEL_REQUESTS_PER_AGENT = 8
MAX_TOOL_CALLS_PER_AGENT = 12


def agent_usage_limits(remaining_tokens: int) -> UsageLimits:
    """Return conservative per-invocation limits with room for normal tool loops.

    Production traces showed a Boss synthesis reaching 13,531 tokens after
    nested specialist reports. The 14,000 ceiling gives that single invocation
    modest headroom while the shared 24,000-token workflow budget remains the
    strict aggregate cap; tool calls and generated output stay independently
    bounded.
    """
    if remaining_tokens <= 0:
        raise ValueError("remaining_tokens must be positive")
    return UsageLimits(
        request_limit=MAX_MODEL_REQUESTS_PER_AGENT,
        tool_calls_limit=MAX_TOOL_CALLS_PER_AGENT,
        output_tokens_limit=MAX_OUTPUT_TOKENS_PER_AGENT_CALL,
        total_tokens_limit=min(MAX_TOKENS_PER_AGENT_CALL, remaining_tokens),
    )


@dataclass
class WorkflowBudget:
    agent_calls: int = 0
    tokens_used: int = 0
    max_agent_calls: int = MAX_AGENT_CALLS
    max_tokens: int = MAX_WORKFLOW_TOKENS


def reset_working_database() -> None:
    """Restore the working copy from the immutable original before a full run."""
    if not ORIGINAL_DATABASE.is_file():
        raise FileNotFoundError(f"Original database not found: {ORIGINAL_DATABASE}")
    if ORIGINAL_DATABASE.resolve() == WORKING_DATABASE.resolve():
        raise RuntimeError("Original and working database paths must be different")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{WORKING_DATABASE}{suffix}").exists():
            raise RuntimeError(
                f"Refusing to reset while a SQLite {suffix} sidecar exists; stop database users first"
            )
    WORKING_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="campus_customs_reset_", suffix=".db", dir=WORKING_DATABASE.parent
    )
    os.close(descriptor)
    try:
        shutil.copy2(ORIGINAL_DATABASE, temporary_name)
        os.replace(temporary_name, WORKING_DATABASE)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


class AgentCoordinator:
    def __init__(
        self,
        agents: dict[AgentRole, Agent[AgentDependencies, AgentReport]],
        budget: WorkflowBudget,
        workflow_id: str | None = None,
    ) -> None:
        self.agents = agents
        self.budget = budget
        self.workflow_id = workflow_id

    def _account_usage(self, deps: AgentDependencies, usage: RunUsage) -> int:
        """Charge only newly observed usage to the shared workflow budget."""
        deps.run_usage = usage
        current_tokens = int(usage.total_tokens or 0)
        if current_tokens < deps.tokens_accounted:
            raise RuntimeError("PydanticAI usage counters moved backwards within one agent run")
        delta = current_tokens - deps.tokens_accounted
        self.budget.tokens_used += delta
        deps.tokens_accounted = current_tokens
        return current_tokens

    def _refresh_parent_limit(self, deps: AgentDependencies) -> None:
        """Shrink an awaiting parent run's limit after a nested call spends tokens."""
        usage = deps.run_usage
        limits = deps.usage_limits
        if not isinstance(usage, RunUsage) or not isinstance(limits, UsageLimits):
            return
        own_tokens = int(usage.total_tokens or 0)
        remaining_workflow_tokens = max(0, self.budget.max_tokens - self.budget.tokens_used)
        # Keep an output/context reserve for the parent to synthesize after a
        # child returns. If that reserve is gone, stop the parent's next model
        # request before it can spend beyond the shared workflow allowance.
        if remaining_workflow_tokens < MIN_PARENT_RESUME_HEADROOM:
            limits.total_tokens_limit = max(0, own_tokens - 1)
            limits.request_limit = usage.requests
            return
        limits.total_tokens_limit = min(
            MAX_TOKENS_PER_AGENT_CALL,
            own_tokens + remaining_workflow_tokens - MIN_PARENT_RESUME_HEADROOM,
        )

    async def run_agent(
        self,
        role: AgentRole,
        task: str,
        *,
        stack: tuple[str, ...],
        ticket_id: int | None,
    ) -> AgentReport:
        if len(stack) >= MAX_DELEGATION_DEPTH:
            raise RuntimeError("Maximum peer-delegation depth reached")
        if self.budget.agent_calls >= self.budget.max_agent_calls:
            raise RuntimeError("Workflow agent-call budget exhausted")
        remaining_tokens = self.budget.max_tokens - self.budget.tokens_used
        if remaining_tokens < MIN_PARENT_RESUME_HEADROOM:
            raise RuntimeError("Workflow token budget exhausted")

        self.budget.agent_calls += 1
        call_number = self.budget.agent_calls
        append_audit_record({
            "event": "agent_run_started", "agent": role,
            "workflow_id": self.workflow_id,
            "parent_agent": stack[-1] if stack else None,
            "delegation_depth": len(stack), "call_number": call_number,
            "ticket_id": ticket_id, "task": task,
        })
        limits = agent_usage_limits(remaining_tokens)
        usage = RunUsage()
        deps = AgentDependencies(
            self, stack + (role,), ticket_id, run_usage=usage, usage_limits=limits
        )
        try:
            result = await self.agents[role].run(
                task,
                deps=deps,
                usage_limits=limits,
                usage=usage,
            )
            usage = result.usage
            tokens = self._account_usage(deps, usage)
            if tokens > int(limits.total_tokens_limit or 0):
                raise UsageLimitExceeded(
                    f"Agent token budget exceeded ({tokens} > {limits.total_tokens_limit})"
                )
            if self.budget.tokens_used > self.budget.max_tokens:
                raise UsageLimitExceeded(
                    "Workflow token budget exceeded "
                    f"({self.budget.tokens_used} > {self.budget.max_tokens})"
                )
            report = result.output
            append_audit_record({
                "event": "agent_run_completed", "agent": role,
                "workflow_id": self.workflow_id,
                "parent_agent": stack[-1] if stack else None,
                "delegation_depth": len(stack), "call_number": call_number,
                "ticket_id": ticket_id, "status": report.status,
                "output": report.model_dump(mode="json"),
                "usage": {"requests": usage.requests, "tool_calls": usage.tool_calls,
                          "total_tokens": tokens},
                "workflow_tokens_used": self.budget.tokens_used,
            })
            return report
        except Exception as exc:
            # PydanticAI updates the supplied RunUsage as requests complete,
            # including when a later turn hits a request/token limit. Preserve
            # that consumption so failures cannot erase budget usage from the
            # workflow's audit or remaining-token calculation.
            tokens = self._account_usage(deps, usage)
            append_audit_record({
                "event": "agent_run_failed", "agent": role,
                "workflow_id": self.workflow_id,
                "parent_agent": stack[-1] if stack else None,
                "delegation_depth": len(stack), "call_number": call_number,
                "ticket_id": ticket_id,
                "error": f"{type(exc).__name__}: {exc}",
                "usage": {
                    "requests": usage.requests,
                    "tool_calls": usage.tool_calls,
                    "total_tokens": tokens,
                },
                "workflow_tokens_used": self.budget.tokens_used,
            })
            raise

    async def delegate(
        self,
        *,
        parent_role: AgentRole,
        target_role: AgentRole,
        task: str,
        parent_deps: AgentDependencies,
    ) -> AgentReport:
        stack = parent_deps.stack
        if not stack or stack[-1] != parent_role:
            raise RuntimeError("Delegation context did not match the requesting agent")
        if target_role == parent_role:
            raise ValueError("Self-delegation is not allowed")
        if target_role in stack:
            raise RuntimeError("Delegation cycle prevented: target already appears in this call chain")
        append_audit_record({
            "event": "agent_delegation", "from_agent": parent_role,
            "workflow_id": self.workflow_id,
            "to_agent": target_role, "ticket_id": parent_deps.ticket_id,
            "delegation_depth": len(stack), "task": task,
        })
        # The parent is paused while a delegated agent runs. Account its usage
        # before allocating the child's budget, then shrink its remaining
        # allowance when the child returns. This prevents a parent from keeping
        # a stale pre-delegation token limit after specialists spend the shared
        # workflow budget.
        if isinstance(parent_deps.run_usage, RunUsage):
            self._account_usage(parent_deps, parent_deps.run_usage)
        if self.budget.tokens_used >= self.budget.max_tokens:
            raise RuntimeError("Workflow token budget exhausted before delegation")
        try:
            return await self.run_agent(
                target_role,
                task,
                stack=stack,
                ticket_id=parent_deps.ticket_id,
            )
        finally:
            self._refresh_parent_limit(parent_deps)


async def _run_boss_workflow(
    *,
    workflow: str,
    boss_task: str,
    ticket_id: int | None,
    reset_database: bool,
    workflow_id: str | None = None,
) -> WorkflowReport:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if not os.environ.get("PORTKEY_API_KEY"):
        raise RuntimeError("PORTKEY_API_KEY must be set in the environment or root .env")
    if not MCP_SERVER_FILE.is_file():
        raise FileNotFoundError(f"MCP server not found: {MCP_SERVER_FILE}")
    command = str(MCP_PYTHON if MCP_PYTHON.is_file() else Path(sys.executable))
    if reset_database:
        reset_working_database()

    append_audit_record({
        "event": "workflow_started", "workflow": workflow,
        "workflow_id": workflow_id, "ticket_id": ticket_id,
        "database_reset": reset_database, "max_agent_calls": MAX_AGENT_CALLS,
        "max_delegation_depth": MAX_DELEGATION_DEPTH,
        "max_workflow_tokens": MAX_WORKFLOW_TOKENS,
    })
    budget = WorkflowBudget()
    mcp_environment = os.environ.copy()
    mcp_environment.pop("PORTKEY_API_KEY", None)
    if workflow_id:
        mcp_environment["CAMPUS_CUSTOMS_WORKFLOW_ID"] = workflow_id
    mcp_server = MCPServerStdio(
        command,
        args=[str(MCP_SERVER_FILE)],
        env=mcp_environment,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    async with mcp_server:
        agents = build_agents(create_portkey_model(), mcp_server)
        coordinator = AgentCoordinator(agents, budget, workflow_id)
        try:
            boss_report = await coordinator.run_agent(
                "boss", boss_task, stack=(), ticket_id=ticket_id
            )
        except Exception as exc:
            append_audit_record({
                "event": "workflow_failed", "workflow": workflow,
                "workflow_id": workflow_id, "ticket_id": ticket_id,
                "error": f"{type(exc).__name__}: {exc}",
                "agent_calls": budget.agent_calls, "tokens_used": budget.tokens_used,
            })
            raise
    summary = WorkflowReport(
        status=boss_report.status,
        summary=boss_report.summary,
        boss_report=boss_report,
        agent_calls=budget.agent_calls,
        tokens_used=budget.tokens_used,
        audit_file="output/audit_trail.json",
    )
    append_audit_record({
        "event": "workflow_completed", "workflow": workflow,
        "workflow_id": workflow_id, "ticket_id": ticket_id,
        "status": summary.status, "summary": summary.summary,
        "agent_calls": summary.agent_calls, "tokens_used": summary.tokens_used,
    })
    return summary


async def resolve_ticket(
    ticket_id: int,
    *,
    workflow_id: str | None = None,
    continuation_after_payment: bool = False,
) -> WorkflowReport:
    """Run Boss and delegated agents for one ticket without resetting the database."""
    if continuation_after_payment:
        task = (
            f"Continue work on Campus Customs ticket {ticket_id} after a human-approved "
            "payment. Re-read the ticket and its linked financial/inventory facts through "
            "MCP; confirm the processed payment is recorded, but never request or process "
            "that obligation again. A paid vendor invoice alone does not fulfill a customer "
            "order: verify actual on-hand SKU/size/quantity and do not infer vendor supply, "
            "shipment, or delivery. If stock is short, use request_human_decision with kind "
            "fulfillment, concrete evidence-backed options and terms, and explain that a "
            "decision resolves only the blocker, not delivery. Never record a human choice. "
            "Delegate only remaining necessary work. mark_ticket_resolved returns the exact "
            "outstanding requirements; only its `status=resolved` result permits you to say "
            "the ticket is resolved. Do not send customer/vendor communications."
        )
    elif ticket_id == 103:
        task = (
            "Handle only Ticket 103, the bulk hoodie price override. Delegate once to "
            "Accounting to read get_ticket_product_context and list_decisions for ticket 103, reuse the "
            "already-decided request for a target unit price, and save the exact fact-matched "
            "agent_note with offer_unit_price=null. Then pass those facts and the existing "
            "choice to Customer Service to save one honest, unsent draft asking for the target; "
            "state 8 of 20 in stock and 12 short, with no discount or delivery promise. Do not "
            "Do not delegate Inventory, repeat lookups, make financial proposals, or call "
            "mark_ticket_resolved. never request kind=fulfillment for this ticket; if no "
            "discount choice exists and a target is missing, request at most one kind=discount "
            "decision. After both saved records are confirmed, "
            "return a concise complete synthesis. FastAPI will run the MCP final-disposition "
            "gate after the workflow completion record; do not claim ticket resolution before it."
        )
    else:
        task = (
            f"Work only on Campus Customs ticket {ticket_id}. Confirm the ticket on the board, "
            "then delegate the factual investigation to the lead specialist before doing any "
            "specialist's MCP lookups yourself; use their returned evidence to avoid duplicate "
            "research. Add a second specialist only when the first report exposes a concrete "
            "cross-discipline need, then review the evidence and give a ticket-specific "
            "disposition. Do not process payments or send communications: an eligible exact "
            "payment may only be prepared for human approval, and customer/vendor wording "
            "must be saved as a ticket-board draft."
        )
    return await _run_boss_workflow(
        workflow="resolve_ticket", boss_task=task, ticket_id=ticket_id,
        reset_database=False, workflow_id=workflow_id,
    )


async def resolve_open_tickets(*, reset_database: bool = True) -> WorkflowReport:
    """Reset (by default), then ask Boss to triage all open tickets."""
    task = (
        "Review the open Campus Customs ticket board by calling the MCP tool "
        "list_open_tickets. For each open ticket, decide its lead specialist, delegate "
        "focused work to the relevant peer agent(s), then review their findings and make "
        "a cautious final disposition. Avoid repeated research. Use MCP for all shop facts. "
        "Leave payment execution and external communication to a human; save any proposed "
        "communication as a ticket-board draft. Return one concise overall report and "
        "identify ticket IDs explicitly."
    )
    return await _run_boss_workflow(
        workflow="resolve_open_tickets", boss_task=task, ticket_id=None,
        reset_database=reset_database,
    )


async def _main() -> None:
    result = await resolve_open_tickets()
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
