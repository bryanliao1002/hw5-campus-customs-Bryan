"""FastAPI routes consumed by the Campus Customs React dashboard."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Literal
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend.audit import append_audit_record, read_audit_records
from backend.mcp_client import MCPToolError, call_mcp_tool
from backend.orchestrator import resolve_ticket, reset_working_database
from backend.recovery import (
    RECOVERABLE_TICKET_IDS,
    RecoveryNotEligible,
    load_verified_current_evidence,
    recovery_candidate_status,
    resume_failed_boss_synthesis,
    find_boss_synthesis_candidate,
)


app = FastAPI(
    title="Campus Customs Operations API",
    version="1.0.0",
    description="Local dashboard API for tickets, agent activity, and human-approved finance actions.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class ApprovalSubmission(BaseModel):
    approved_by: str = Field(min_length=1, max_length=80)
    confirm: bool
    confirm_vendor_can_supply: bool = False

    @field_validator("approved_by")
    @classmethod
    def normalize_approver(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter the human approver's name or identifier")
        return value


class FinancialRejectionSubmission(BaseModel):
    rejected_by: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("rejected_by", "reason")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A non-empty reviewer name and reason are required")
        return value


class HumanDecisionSubmission(BaseModel):
    decided_by: str = Field(min_length=1, max_length=80)
    choice: str = Field(min_length=1, max_length=160)
    terms: str = Field(min_length=1, max_length=2000)

    @field_validator("decided_by", "choice", "terms")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Approver, choice, and terms must not be empty")
        return value


class SynthesisRecoverySubmission(BaseModel):
    failed_workflow_id: str = Field(min_length=1, max_length=80)

    @field_validator("failed_workflow_id")
    @classmethod
    def normalize_workflow_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("The exact failed workflow ID is required")
        return value


_runs: dict[str, dict[str, Any]] = {}
_run_tasks: dict[str, asyncio.Task[None]] = {}


def _ticket_has_active_run(ticket_id: int) -> bool:
    return any(
        item.get("ticket_id") == ticket_id
        and item.get("status") in {"queued", "running"}
        for item in _runs.values()
    )


async def _shop_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    try:
        return await call_mcp_tool(name, arguments)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MCP tool {name} failed: {type(exc).__name__}: {exc}",
        ) from exc


async def _finalize_ticket_if_ready(ticket_id: int, note: str) -> dict[str, Any]:
    """Ask the standard MCP final-disposition gate after a human decision/action."""
    try:
        return await _shop_tool(
            "mark_ticket_resolved",
            {"ticket_id": ticket_id, "resolution_note": note[:1000]},
        )
    except HTTPException as exc:
        append_audit_record({
            "event": "final_disposition_check_failed", "ticket_id": ticket_id,
            "error": exc.detail,
        })
        return {"ticket_id": ticket_id, "status": "open", "resolved": False, "error": exc.detail}


def _activity_item(record: dict[str, Any]) -> dict[str, Any]:
    event = record.get("event", "unknown")
    output = record.get("output") or {}
    if event == "agent_run_completed":
        message = output.get("summary", "Agent completed a turn")
    elif event == "agent_run_started":
        message = record.get("task", "Agent started")
    elif event == "agent_delegation":
        message = record.get("task", "Peer delegation")
    elif event == "mcp_tool":
        message = f"MCP {record.get('tool', 'tool')} {record.get('outcome', 'called')}"
    else:
        message = record.get("summary") or record.get("error") or event.replace("_", " ")
    tool_inputs = record.get("inputs") or {}
    ticket_id = record.get("ticket_id")
    if ticket_id is None and isinstance(tool_inputs, dict):
        ticket_id = tool_inputs.get("ticket_id")
    return {
        "timestamp": record.get("timestamp"),
        "event": event,
        "workflow_id": record.get("workflow_id"),
        "ticket_id": ticket_id,
        "agent": record.get("agent") or record.get("from_agent"),
        "delegated_to": record.get("to_agent"),
        "message": message,
        "agent_output": output or None,
        "mcp_tool": record.get("tool"),
        "tool_inputs": tool_inputs or None,
        "tool_result": record.get("result"),
        "status": record.get("status") or output.get("status"),
        "error": record.get("error"),
        "usage": record.get("usage"),
    }


async def _run_ticket_in_background(
    run_id: str,
    ticket_id: int,
    *,
    continuation_after_payment: bool = False,
    continuation_of: str | None = None,
    recovery_of: str | None = None,
) -> None:
    run = _runs[run_id]
    run["status"] = "running"
    run["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        if recovery_of is not None:
            result = await resume_failed_boss_synthesis(
                ticket_id,
                failed_workflow_id=recovery_of,
                recovery_workflow_id=run_id,
            )
        else:
            result = await resolve_ticket(
                ticket_id,
                workflow_id=run_id,
                continuation_after_payment=continuation_after_payment,
            )
        effective_status = result.status
        effective_summary = result.summary
        disposition = await _finalize_ticket_if_ready(ticket_id, result.summary)
        if disposition.get("status") == "resolved":
            effective_status = "complete"
            effective_summary = result.summary
        elif result.status == "complete":
            effective_status = "blocked"
            effective_summary = (
                "The agent reported completion, but final-disposition requirements remain: "
                + "; ".join(disposition.get("outstanding", []))
                if disposition.get("outstanding") else
                f"The agent reported completion, but final disposition could not be verified: {disposition.get('error', 'no evidence returned')}"
            )
        database_ticket_status = None
        if effective_status == "complete":
            tickets = await _shop_tool("list_tickets")
            ticket = next((item for item in tickets if item.get("id") == ticket_id), None)
            database_ticket_status = str(ticket.get("status", "unknown")).casefold() if ticket else "missing"
            if database_ticket_status != "resolved":
                effective_status = "blocked"
                effective_summary = (
                    "The agent reported completion, but the MCP ticket record is not resolved. "
                    "The run is not counted as complete; verify the required status-tool evidence."
                )
                append_audit_record({
                    "event": "api_ticket_completion_rejected",
                    "workflow_id": run_id,
                    "ticket_id": ticket_id,
                    "reported_status": result.status,
                    "database_ticket_status": database_ticket_status,
                })
        run.update({
            "status": effective_status,
            "summary": effective_summary,
            "agent_calls": result.agent_calls,
            "tokens_used": result.tokens_used,
            "result": result.model_dump(mode="json"),
            "database_ticket_status": database_ticket_status,
            "final_disposition": disposition,
        })
        append_audit_record({
            "event": "api_ticket_run_finished", "workflow_id": run_id,
            "ticket_id": ticket_id, "status": effective_status,
            "model_status": result.status,
            "database_ticket_status": database_ticket_status,
            "summary": effective_summary,
            "continuation_after_payment": continuation_after_payment,
            "continuation_of": continuation_of,
            "recovery_of": recovery_of,
            "synthesis_recovery": recovery_of is not None,
            "agent_calls": result.agent_calls, "tokens_used": result.tokens_used,
        })
    except Exception as exc:
        run.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        append_audit_record({
            "event": "api_ticket_run_failed", "workflow_id": run_id,
            "ticket_id": ticket_id,
            "continuation_after_payment": continuation_after_payment,
            "continuation_of": continuation_of,
            "recovery_of": recovery_of,
            "error": f"{type(exc).__name__}: {exc}",
        })
    finally:
        run["finished_at"] = datetime.now(timezone.utc).isoformat()
        _run_tasks.pop(run_id, None)


def _queue_ticket_run(
    ticket_id: int,
    *,
    continuation_after_payment: bool = False,
    continuation_of: str | None = None,
) -> dict[str, Any]:
    run_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc).isoformat()
    run = {
        "run_id": run_id,
        "ticket_id": ticket_id,
        "status": "queued",
        "submitted_at": submitted_at,
        "continuation_after_payment": continuation_after_payment,
        "continuation_of": continuation_of,
    }
    _runs[run_id] = run
    append_audit_record({
        "event": (
            "api_ticket_continuation_requested"
            if continuation_after_payment else "api_ticket_run_requested"
        ),
        "workflow_id": run_id,
        "ticket_id": ticket_id,
        "continuation_of": continuation_of,
        "submitted_at": submitted_at,
    })
    _run_tasks[run_id] = asyncio.create_task(
        _run_ticket_in_background(
            run_id,
            ticket_id,
            continuation_after_payment=continuation_after_payment,
            continuation_of=continuation_of,
        )
    )
    return run


def _queue_boss_synthesis_recovery(
    ticket_id: int,
    failed_workflow_id: str,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Queue one explicit Boss-only recovery tied to its verified failed source."""
    run_id = run_id or str(uuid4())
    submitted_at = datetime.now(timezone.utc).isoformat()
    run = {
        "run_id": run_id,
        "ticket_id": ticket_id,
        "status": "queued",
        "submitted_at": submitted_at,
        "synthesis_recovery": True,
        "recovery_of": failed_workflow_id,
    }
    _runs[run_id] = run
    # The existing MCP final-disposition gate treats this as the latest ticket
    # workflow. resume_failed_boss_synthesis will append its matching start and
    # completion records before the ordinary gate is called.
    append_audit_record({
        "event": "api_ticket_run_requested",
        "workflow_id": run_id,
        "ticket_id": ticket_id,
        "recovery_of": failed_workflow_id,
        "synthesis_recovery": True,
        "submitted_at": submitted_at,
    })
    _run_tasks[run_id] = asyncio.create_task(
        _run_ticket_in_background(
            run_id,
            ticket_id,
            recovery_of=failed_workflow_id,
        )
    )
    return run


async def _ticket_continuation_status(ticket_id: int) -> dict[str, Any]:
    tickets = await _shop_tool("list_tickets")
    ticket = next((item for item in tickets if item.get("id") == ticket_id), None)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if str(ticket.get("status", "")).casefold() != "open":
        return {"ticket_id": ticket_id, "eligible": False,
                "reason": "Only an open ticket can continue."}
    if _ticket_has_active_run(ticket_id):
        return {"ticket_id": ticket_id, "eligible": False,
                "reason": "An agent run is already active for this ticket."}

    history = read_audit_records(limit=20_000)
    latest_end = next((record for record in reversed(history)
                       if record.get("ticket_id") == ticket_id
                       and record.get("event") in {
                           "api_ticket_run_finished", "api_ticket_run_failed"
                       }), None)
    if not latest_end or latest_end.get("event") != "api_ticket_run_finished" \
            or latest_end.get("status") != "needs_human":
        return {"ticket_id": ticket_id, "eligible": False,
                "reason": "No latest needs-human run is awaiting a processed payment."}

    prior_workflow_id = latest_end.get("workflow_id")
    successful_requests = {
        record.get("result", {}).get("request_id")
        for record in history
        if record.get("event") == "mcp_tool"
        and record.get("workflow_id") == prior_workflow_id
        and record.get("tool") == "request_payment_approval"
        and record.get("outcome") == "success"
        and record.get("result", {}).get("ticket_id") == ticket_id
        and record.get("result", {}).get("status") == "pending_approval"
    }
    successful_requests.discard(None)
    if not successful_requests:
        return {"ticket_id": ticket_id, "eligible": False,
                "reason": "The prior needs-human run did not create a payment proposal."}

    context = await _shop_tool("get_ticket_financial_context", {"ticket_id": ticket_id})
    requests = context.get("ticket_payment_requests", [])
    payments = context.get("recorded_payments", [])
    processed: list[dict[str, Any]] = []
    for payment_request in requests:
        if (payment_request.get("id") not in successful_requests
                or payment_request.get("status") != "processed"):
            continue
        match = next((payment for payment in payments
                      if payment.get("kind") == payment_request.get("kind")
                      and payment.get("ref_id") == payment_request.get("ref_id")
                      and payment.get("account") == payment_request.get("account")
                      and round(float(payment.get("amount", 0)) * 100)
                      == round(float(payment_request.get("amount", 0)) * 100)), None)
        if match:
            processed.append({
                "request_id": payment_request["id"],
                "payment_id": match["id"],
                "kind": match["kind"],
                "ref_id": match["ref_id"],
                "amount": match["amount"],
            })
    pending = any(item.get("status") == "pending_approval" for item in requests)
    pending = pending or any(
        item.get("status") == "pending_approval"
        for item in context.get("ticket_purchase_requests", [])
    )
    if pending:
        return {"ticket_id": ticket_id, "eligible": False,
                "reason": "Resolve every pending financial approval before continuing."}
    if not processed:
        return {"ticket_id": ticket_id, "eligible": False,
                "reason": "The proposal from the latest needs-human run has not been recorded as paid."}
    return {
        "ticket_id": ticket_id,
        "eligible": True,
        "reason": "A payment proposed by the latest needs-human run is recorded as processed. "
                  "Continuing will re-check remaining requirements and will not reprocess it.",
        "continuation_of": prior_workflow_id,
        "processed_payments": processed,
    }


@app.get("/api/tickets")
async def get_tickets() -> dict[str, Any]:
    """Return all ticket records and explicit open/resolved flags."""
    tickets = await _shop_tool("list_tickets")
    result = []
    for ticket in tickets:
        current_status = str(ticket.get("status", "unknown"))
        normalized = current_status.casefold()
        resolved = normalized in {"resolved", "closed", "complete", "completed"}
        result.append({**ticket, "is_open": normalized == "open", "is_resolved": resolved})
    return {"tickets": result, "count": len(result)}


@app.post("/api/tickets/{ticket_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def start_ticket_run(ticket_id: int) -> dict[str, Any]:
    """Start the agent team asynchronously for one open ticket; do not reset the DB."""
    tickets = await _shop_tool("list_tickets")
    ticket = next((item for item in tickets if item.get("id") == ticket_id), None)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if str(ticket.get("status", "")).casefold() != "open":
        raise HTTPException(status_code=409, detail="Only an open ticket can be started")
    if _ticket_has_active_run(ticket_id):
        raise HTTPException(status_code=409, detail="An agent run is already active for this ticket")
    recovery_state = recovery_candidate_status(ticket_id)
    if recovery_state.get("eligible") or recovery_state.get("in_progress"):
        raise HTTPException(
            status_code=409,
            detail=("A Boss-synthesis recovery is available or already in progress for this ticket; "
                    "use the explicit recovery action instead of rerunning completed specialists."),
        )

    return _queue_ticket_run(ticket_id)


@app.get("/api/tickets/{ticket_id}/recovery-candidate")
async def get_synthesis_recovery_candidate(ticket_id: int) -> dict[str, Any]:
    """Inspect audit evidence only; does not call a model or alter shop state."""
    return recovery_candidate_status(ticket_id)


@app.post("/api/tickets/{ticket_id}/recover-synthesis", status_code=status.HTTP_202_ACCEPTED)
async def recover_boss_synthesis(
    ticket_id: int,
    request: Request,
    submission: SynthesisRecoverySubmission,
) -> dict[str, Any]:
    """Explicitly resume Boss synthesis after persisted and live evidence pass."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Synthesis recovery is local-only")
    if ticket_id not in RECOVERABLE_TICKET_IDS:
        raise HTTPException(status_code=404, detail="No synthesis recovery is available for this ticket")
    if _ticket_has_active_run(ticket_id):
        raise HTTPException(status_code=409, detail="An agent run is already active for this ticket")
    try:
        candidate = find_boss_synthesis_candidate(
            ticket_id,
            failed_workflow_id=submission.failed_workflow_id,
        )
    except RecoveryNotEligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Confirm current prerequisites before queueing any model request. The
    # background runner repeats these MCP reads immediately before Boss starts.
    recovery_workflow_id = str(uuid4())
    try:
        await load_verified_current_evidence(
            candidate,
            recovery_workflow_id,
        )
    except RecoveryNotEligible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Recovery preflight could not verify current MCP evidence: {type(exc).__name__}: {exc}",
        ) from exc

    if _ticket_has_active_run(ticket_id):
        raise HTTPException(status_code=409, detail="An agent run is already active for this ticket")
    append_audit_record({
        "event": "api_ticket_recovery_preflight_passed",
        "workflow_id": recovery_workflow_id,
        "ticket_id": ticket_id,
        "recovery_of": candidate.workflow_id,
        "saved_specialists": list(candidate.reports),
    })
    queued = _queue_boss_synthesis_recovery(
        ticket_id,
        candidate.workflow_id,
        run_id=recovery_workflow_id,
    )
    return queued


@app.get("/api/tickets/{ticket_id}/continuation")
async def get_ticket_continuation(ticket_id: int) -> dict[str, Any]:
    """Tell the dashboard whether a needs-human run can safely resume after payment."""
    return await _ticket_continuation_status(ticket_id)


@app.post("/api/tickets/{ticket_id}/continue", status_code=status.HTTP_202_ACCEPTED)
async def continue_ticket_after_payment(ticket_id: int) -> dict[str, Any]:
    """Explicitly resume only after the prior proposal is verified as processed."""
    eligibility = await _ticket_continuation_status(ticket_id)
    if not eligibility.get("eligible"):
        raise HTTPException(status_code=409, detail=eligibility.get("reason"))
    # Eligibility performs asynchronous MCP checks. Recheck synchronously just
    # before queueing so two concurrent Continue clicks cannot launch duplicate runs.
    if _ticket_has_active_run(ticket_id):
        raise HTTPException(status_code=409, detail="An agent run is already active for this ticket")
    return _queue_ticket_run(
        ticket_id,
        continuation_after_payment=True,
        continuation_of=eligibility.get("continuation_of"),
    )


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """Return queued/running/completed status for one asynchronous ticket run."""
    run = _runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found (or backend reloaded)")
    return run


@app.get("/api/activity")
async def get_recent_activity(
    limit: int = Query(default=80, ge=1, le=200),
    ticket_id: int | None = Query(default=None, ge=1),
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Return recent agent statements, peer delegations, and MCP tool use/results."""
    try:
        records = read_audit_records(limit=max(limit * 3, 100))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read audit activity: {exc}") from exc
    selected = []
    for record in records:
        item = _activity_item(record)
        if ticket_id is not None and item["ticket_id"] != ticket_id:
            continue
        if workflow_id is not None and item["workflow_id"] != workflow_id:
            continue
        selected.append(item)
    return {"activity": selected[-limit:], "count": min(len(selected), limit)}


@app.get("/api/approvals")
async def get_pending_approvals() -> dict[str, Any]:
    """List queued payment and purchase proposals for the human dashboard."""
    return await _shop_tool("list_pending_approval_requests")


@app.get("/api/decisions")
async def get_human_decisions(
    ticket_id: int | None = Query(default=None, ge=1),
    decision_status: Literal["pending", "decided"] | None = Query(default=None, alias="status"),
) -> dict[str, Any]:
    """Return the separate JSON-backed human decision queue through MCP."""
    decisions = await _shop_tool(
        "list_decisions", {"ticket_id": ticket_id, "status": decision_status}
    )
    return {"decisions": decisions, "count": len(decisions)}


@app.post("/api/decisions/{decision_id}")
async def decide_human_request(
    decision_id: str,
    request: Request,
    submission: HumanDecisionSubmission,
) -> dict[str, Any]:
    """Record a human choice, then independently re-evaluate ticket final disposition."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Human decisions are local-only")
    try:
        decision = await call_mcp_tool(
            "record_human_decision",
            {
                "decision_id": decision_id,
                "decided_by": submission.decided_by,
                "choice": submission.choice,
                "terms": submission.terms,
            },
            approval_server=True,
        )
    except MCPToolError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    disposition = await _finalize_ticket_if_ready(
        decision["ticket_id"],
        f"Human fulfillment/disposition recorded: {decision['choice']}. Terms: {decision['terms']}",
    )
    return {"decision": decision, "final_disposition": disposition}


@app.post("/api/approvals/{request_type}/{request_id}/approve")
async def approve_request(
    request_type: Literal["payment", "purchase"],
    request_id: int,
    request: Request,
    submission: ApprovalSubmission,
) -> dict[str, Any]:
    """Require an explicit human confirmation, then invoke the isolated approval MCP."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Human financial approval is local-only")
    if not submission.confirm:
        raise HTTPException(status_code=400, detail="Explicit human confirmation is required")
    if request_type == "purchase" and not submission.confirm_vendor_can_supply:
        raise HTTPException(
            status_code=400,
            detail="Confirm that the vendor can supply this exact SKU and size before approval",
        )
    try:
        result = await call_mcp_tool(
            "approve_and_process",
            {
                "request_type": request_type,
                "request_id": request_id,
                "approved_by": submission.approved_by,
                "confirm_vendor_can_supply": submission.confirm_vendor_can_supply,
            },
            approval_server=True,
        )
        disposition = await _finalize_ticket_if_ready(
            result["ticket_id"],
            f"Financial request {result['request_id']} was approved and processed by {submission.approved_by}.",
        )
        return {**result, "final_disposition": disposition}
    except MCPToolError as exc:
        append_audit_record({
            "event": "human_approval_rejected", "request_type": request_type,
            "request_id": request_id, "attempted_by": submission.approved_by,
            "reason": str(exc),
        })
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Approval processing failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.post("/api/approvals/{request_type}/{request_id}/reject")
async def reject_financial_request(
    request_type: Literal["payment", "purchase"],
    request_id: int,
    request: Request,
    submission: FinancialRejectionSubmission,
) -> dict[str, Any]:
    """Allow a local human to explicitly reject a pending finance proposal."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Financial decisions are local-only")
    try:
        result = await call_mcp_tool(
            "reject_financial_request",
            {
                "request_type": request_type, "request_id": request_id,
                "rejected_by": submission.rejected_by, "reason": submission.reason,
            },
            approval_server=True,
        )
        disposition = await _finalize_ticket_if_ready(
            result["ticket_id"],
            f"Financial request {request_id} was rejected by {submission.rejected_by}: {submission.reason}",
        )
        return {**result, "final_disposition": disposition}
    except MCPToolError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/cash/checking")
async def get_checking_balance() -> dict[str, Any]:
    """Return the current checking balance through the standard MCP server."""
    return await _shop_tool("get_cash_balance", {"account_name": "checking"})


@app.post("/api/database/reset")
async def reset_database(request: Request) -> dict[str, Any]:
    """Restore the working SQLite file from the untouched original starting database."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Database reset is local-only")
    active = [run_id for run_id, run in _runs.items() if run.get("status") in {"queued", "running"}]
    if active:
        raise HTTPException(status_code=409, detail="Cannot reset while an agent workflow is active")
    try:
        reset_working_database()
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Database reset refused: {exc}") from exc
    reset_at = datetime.now(timezone.utc).isoformat()
    append_audit_record({"event": "api_database_reset", "reset_at": reset_at})
    return {"status": "reset", "reset_at": reset_at, "audit_trail_preserved": True}
