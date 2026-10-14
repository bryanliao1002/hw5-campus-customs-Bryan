"""Typed inputs and outputs shared by the Campus Customs agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AgentRole = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]
TicketStatus = Literal["complete", "needs_human", "blocked", "in_progress"]


class AgentReport(BaseModel):
    """A short, auditable result from one bounded agent turn."""

    status: TicketStatus
    summary: str = Field(min_length=1, max_length=1800)
    findings: list[str] = Field(default_factory=list, max_length=8)
    decisions: list[str] = Field(default_factory=list, max_length=8)
    next_steps: list[str] = Field(default_factory=list, max_length=8)
    human_approval_required: bool = False


class AgentDependencies:
    """Per-run dependencies; workflow budget is shared by every delegated call."""

    def __init__(
        self,
        coordinator: object,
        stack: tuple[str, ...],
        ticket_id: int | None,
        *,
        run_usage: object | None = None,
        usage_limits: object | None = None,
    ):
        self.coordinator = coordinator
        self.stack = stack
        self.ticket_id = ticket_id
        self.run_usage = run_usage
        self.usage_limits = usage_limits
        self.tokens_accounted = 0


class WorkflowReport(BaseModel):
    status: TicketStatus
    summary: str
    boss_report: AgentReport
    agent_calls: int = Field(ge=1)
    tokens_used: int = Field(ge=0)
    audit_file: str
