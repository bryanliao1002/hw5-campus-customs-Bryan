"""Evidence-gated Boss-only recovery from persisted specialist work."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Any, Callable

from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage, UsageLimits

from backend.agents import create_portkey_model
from backend.audit import append_audit_record, read_audit_records
from backend.mcp_client import call_mcp_tool
from backend.models import AgentReport, WorkflowReport


RECOVERABLE_TICKET_ID = 101  # Backwards-compatible alias used by older tests/tools.
RECOVERABLE_TICKET_IDS = {101, 103}
REQUIRED_SAVED_SPECIALISTS_BY_TICKET = {
    101: ("inventory", "accounting", "customer_service"),
    103: ("accounting", "customer_service"),
}
REQUIRED_SAVED_SPECIALISTS = REQUIRED_SAVED_SPECIALISTS_BY_TICKET[101]
RECOVERY_MAX_TOKENS = 6_000
RECOVERY_MAX_OUTPUT_TOKENS = 1_200
RECOVERY_MAX_REQUESTS = 3
PRICE_OVERRIDE_RECOVERY_MAX_TOKENS = 3_000
PRICE_OVERRIDE_RECOVERY_MAX_OUTPUT_TOKENS = 700
PRICE_OVERRIDE_RECOVERY_MAX_REQUESTS = 2
_TERMINAL_FINANCIAL_STATES = {"processed", "approved", "rejected"}

RECOVERY_INSTRUCTIONS = """You are Boss performing only a concise final synthesis for a failed
Campus Customs workflow. The supplied specialist reports are exact persisted
outputs from that failed workflow; current MCP evidence is authoritative when
facts changed afterward. Confirm that evidence supports the ticket's existing
human decision and business disposition. For Ticket 101, say fulfillment is
deferred pending exact-item confirmation, no delivery date is promised, and
receipt/delivery is not evidenced. For Ticket 103, say the recorded choice is
to ask for a target unit price and the saved customer message remains an unsent
draft; do not quote a discount or imply all 20 units can be supplied. Return
complete only if every preflight fact is present; otherwise return blocked and
name the missing evidence. Keep the report brief (one short summary and at most
two findings, decisions, and next steps). You have no tools and must not
delegate, write to the board, request/record decisions, propose or process
payments, change inventory, contact anyone, or mark the ticket resolved. The
backend will run the ordinary MCP final-disposition gate after this recovery
workflow is recorded complete; this report does not make the original failed
workflow successful."""


class RecoveryNotEligible(ValueError):
    """A failed run or its current prerequisites do not support safe recovery."""


@dataclass(frozen=True)
class BossSynthesisCandidate:
    ticket_id: int
    workflow_id: str
    workflow_started_at: str | None
    reports: dict[str, AgentReport]
    board_write_ids: dict[str, int] | None = None

    def compact_evidence(self) -> dict[str, Any]:
        return {
            "source_workflow_id": self.workflow_id,
            "source_workflow_started_at": self.workflow_started_at,
            "saved_specialist_reports": {
                # Omit empty default arrays and other default values from the
                # persisted reports. The included text remains verbatim saved
                # evidence; this only avoids paying to resend empty fields.
                role: report.model_dump(mode="json", exclude_defaults=True)
                for role, report in self.reports.items()
            },
            **({"verified_board_update_ids": self.board_write_ids}
               if self.board_write_ids else {}),
        }


def find_boss_synthesis_candidate(
    ticket_id: int,
    failed_workflow_id: str | None = None,
    *,
    history: list[dict[str, Any]] | None = None,
    allow_recovery_workflow_id: str | None = None,
) -> BossSynthesisCandidate:
    """Verify saved specialists and source-run writes for an eligible failed Boss synthesis."""
    if ticket_id not in RECOVERABLE_TICKET_IDS:
        raise RecoveryNotEligible("Synthesis recovery is currently limited to Tickets 101 and 103.")
    records = history if history is not None else read_audit_records(limit=20_000)
    terminal = next((row for row in reversed(records)
                     if row.get("ticket_id") == ticket_id
                     and row.get("event") in {"api_ticket_run_finished", "api_ticket_run_failed"}), None)
    if not terminal or terminal.get("event") != "api_ticket_run_failed":
        raise RecoveryNotEligible(f"The latest Ticket {ticket_id} run is not a failed workflow.")
    source_id = terminal.get("workflow_id")
    if not isinstance(source_id, str) or (failed_workflow_id and failed_workflow_id != source_id):
        raise RecoveryNotEligible(f"The requested failed workflow is not the latest Ticket {ticket_id} run.")

    for request in records:
        recovery_id = request.get("workflow_id")
        if not (
            request.get("event") == "api_ticket_run_requested"
            and request.get("ticket_id") == ticket_id
            and request.get("synthesis_recovery") is True
            and request.get("recovery_of") == source_id
            and recovery_id != allow_recovery_workflow_id
        ):
            continue
        has_terminal = any(
            row.get("workflow_id") == recovery_id
            and row.get("ticket_id") == ticket_id
            and row.get("event") in {"api_ticket_run_finished", "api_ticket_run_failed"}
            for row in records
        )
        if not has_terminal:
            raise RecoveryNotEligible(
                "A Boss-synthesis recovery is already queued or running for this failed workflow."
            )

    started = next((row for row in records if row.get("event") == "workflow_started"
                    and row.get("workflow_id") == source_id
                    and row.get("ticket_id") == ticket_id
                    and row.get("workflow") == "resolve_ticket"), None)
    workflow_failed = next((row for row in reversed(records)
                            if row.get("event") == "workflow_failed"
                            and row.get("workflow_id") == source_id
                            and row.get("ticket_id") == ticket_id), None)
    boss_failure_entry = next(((index, row) for index, row in reversed(list(enumerate(records)))
                               if row.get("event") == "agent_run_failed"
                               and row.get("workflow_id") == source_id
                               and row.get("ticket_id") == ticket_id
                               and row.get("agent") == "boss"), None)
    boss_failure_index, boss_failure = boss_failure_entry or (None, None)
    if not started or not workflow_failed or not boss_failure:
        raise RecoveryNotEligible("The audit does not prove a failed Boss synthesis for this workflow.")
    failure_text = str(boss_failure.get("error", ""))
    if "UsageLimitExceeded" not in failure_text or "total_tokens_limit" not in failure_text:
        raise RecoveryNotEligible("Only a Boss failure caused by the token limit can use this recovery path.")
    if any(row.get("event") == "workflow_completed" and row.get("workflow_id") == source_id
           and row.get("ticket_id") == ticket_id for row in records):
        raise RecoveryNotEligible("The source workflow already has a completion record.")

    required_specialists = REQUIRED_SAVED_SPECIALISTS_BY_TICKET[ticket_id]
    reports: dict[str, AgentReport] = {}
    for role in required_specialists:
        matching = [(index, row) for index, row in enumerate(records)
                    if row.get("event") == "agent_run_completed"
                    and row.get("workflow_id") == source_id
                    and row.get("ticket_id") == ticket_id
                    and row.get("agent") == role
                    and row.get("parent_agent") == "boss"
                    and row.get("delegation_depth") == 1]
        failed = any(row.get("event") == "agent_run_failed"
                     and row.get("workflow_id") == source_id
                     and row.get("ticket_id") == ticket_id
                     and row.get("agent") == role for row in records)
        if len(matching) != 1 or failed or matching[0][0] >= boss_failure_index:
            raise RecoveryNotEligible(
                f"Recovery requires exactly one saved successful {role} report before Boss failed, and no failed {role} run."
            )
        try:
            reports[role] = AgentReport.model_validate(matching[0][1].get("output"))
        except Exception as exc:
            raise RecoveryNotEligible(f"The saved {role} result is missing or invalid.") from exc

    board_write_ids: dict[str, int] = {}
    if ticket_id == 103:
        # This discount-only workflow must not create financial side effects,
        # and recovery must never repeat a write already completed by a peer.
        if any(
            row.get("event") == "mcp_tool"
            and row.get("workflow_id") == source_id
            and row.get("tool") in {
                "request_payment_approval", "request_purchase_approval",
                "approve_and_process", "append_ticket_board_item",
                "request_human_decision",
            }
            and row.get("tool") != "append_ticket_board_item"
            and row.get("outcome") == "success"
            for row in records
        ):
            raise RecoveryNotEligible(
                "Ticket 103 source workflow contains an unexpected financial or decision write."
            )
        expected_items = {
            "accounting": ("agent_note", "Accounting"),
            "customer_service": ("customer_message_draft", "Customer Service"),
        }
        for role, (item_type, author) in expected_items.items():
            role_start = next((index for index, row in enumerate(records)
                               if row.get("event") == "agent_run_started"
                               and row.get("workflow_id") == source_id
                               and row.get("ticket_id") == ticket_id
                               and row.get("agent") == role), None)
            role_end = next((index for index, row in enumerate(records)
                             if row.get("event") == "agent_run_completed"
                             and row.get("workflow_id") == source_id
                             and row.get("ticket_id") == ticket_id
                             and row.get("agent") == role), None)
            writes = [
                row for index, row in enumerate(records)
                if role_start is not None and role_end is not None
                and role_start < index < role_end
                and row.get("event") == "mcp_tool"
                and row.get("workflow_id") == source_id
                and row.get("tool") == "append_ticket_board_item"
                and row.get("outcome") == "success"
                and (row.get("inputs") or {}).get("ticket_id") == ticket_id
                and (row.get("result") or {}).get("ticket_id") == ticket_id
                and (row.get("result") or {}).get("item_type") == item_type
                and (row.get("result") or {}).get("author") == author
                and (row.get("result") or {}).get("sent_externally") is False
            ]
            if len(writes) != 1:
                raise RecoveryNotEligible(
                    f"Recovery requires exactly one persisted {item_type} write by {author} in the failed run."
                )
            update_id = (writes[0].get("result") or {}).get("update_id")
            if not isinstance(update_id, int):
                raise RecoveryNotEligible(f"The saved {author} board write has no valid update ID.")
            board_write_ids[role] = update_id

    return BossSynthesisCandidate(
        ticket_id=ticket_id,
        workflow_id=source_id,
        workflow_started_at=started.get("timestamp"),
        reports=reports,
        board_write_ids=board_write_ids or None,
    )


def verify_recovery_prerequisites(
    candidate: BossSynthesisCandidate,
    *,
    tickets: list[dict[str, Any]],
    financial: dict[str, Any],
    decisions: list[dict[str, Any]],
    product: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed unless current MCP reads satisfy the ticket-specific business gates."""
    if candidate.ticket_id == 103:
        return _verify_ticket_103_prerequisites(
            candidate,
            tickets=tickets,
            financial=financial,
            decisions=decisions,
            product=product,
        )
    ticket = next((row for row in tickets if row.get("id") == candidate.ticket_id), None)
    if not ticket or ticket.get("type") != "customer_order" or str(ticket.get("status", "")).casefold() != "open":
        raise RecoveryNotEligible("Ticket 101 must still be an open customer order.")
    if ticket.get("invoice_id") != 501 or ticket.get("sku") != "CC-TEE-WHITE" \
            or ticket.get("size") != "S" or ticket.get("qty") != 1:
        raise RecoveryNotEligible("Ticket 101 no longer matches the saved recovery evidence.")

    invoice = financial.get("linked_invoice") or {}
    if invoice.get("id") != 501 or str(invoice.get("status", "")).casefold() != "paid" \
            or round(float(invoice.get("amount", 0)) * 100) != 84_000:
        raise RecoveryNotEligible("Linked invoice 501 is not verifiably paid for exactly $840.")

    requests = financial.get("ticket_payment_requests", [])
    invoice_requests = [row for row in requests
                        if row.get("kind") == "invoice" and row.get("ref_id") == 501]
    if len(invoice_requests) != 1 or invoice_requests[0].get("status") != "processed" \
            or round(float(invoice_requests[0].get("amount", 0)) * 100) != 84_000:
        raise RecoveryNotEligible("Invoice 501 must have exactly one processed $840 request.")
    if any(str(row.get("status", "")).casefold() not in _TERMINAL_FINANCIAL_STATES
           for row in requests + financial.get("ticket_purchase_requests", [])):
        raise RecoveryNotEligible("A payment or purchase request for Ticket 101 is still unresolved.")

    recorded = [row for row in financial.get("recorded_payments", [])
                if row.get("kind") == "invoice" and row.get("ref_id") == 501]
    matches = [row for row in recorded
               if round(float(row.get("amount", 0)) * 100) == 84_000
               and row.get("account") == "checking" and row.get("approved_by")]
    if len(recorded) != 1 or len(matches) != 1:
        raise RecoveryNotEligible("Exactly one human-approved $840 payment for invoice 501 must be recorded.")
    checking = next((row for row in financial.get("cash_accounts", [])
                     if row.get("name") == "checking"), None)
    if not checking or round(float(checking.get("balance", -1)) * 100) != 256_000:
        raise RecoveryNotEligible("Checking must still be $2,560 before this narrow recovery.")

    fulfillment = [row for row in decisions
                   if row.get("ticket_id") == candidate.ticket_id
                   and row.get("kind") == "fulfillment"]
    decided = [row for row in fulfillment
               if row.get("status") == "decided" and row.get("decided_by")
               and str(row.get("terms", "")).strip()
               and row.get("choice") in row.get("options", [])]
    if len(fulfillment) != 1 or len(decided) != 1:
        raise RecoveryNotEligible("Ticket 101 needs exactly one valid, decided fulfillment record.")

    if product.get("ticket_id") != candidate.ticket_id or product.get("sku") != "CC-TEE-WHITE" \
            or product.get("size") != "S" or product.get("requested_qty") != 1 \
            or product.get("available_qty") != 0 or product.get("shortfall_qty") != 1:
        raise RecoveryNotEligible("Current MCP inventory facts no longer match the recorded size-S shortfall.")

    return {
        "ticket": {key: ticket.get(key) for key in
                   ("id", "type", "subject", "invoice_id", "sku", "size", "qty", "status")},
        "financial": {
            "invoice": invoice,
            "payment_request": invoice_requests[0],
            "recorded_payment": matches[0],
            "checking_balance": checking["balance"],
        },
        "fulfillment_decision": decided[0],
        "product": {
            key: product.get(key) for key in
            ("sku", "size", "requested_qty", "available_qty", "shortfall_qty", "stock_status")
        },
    }


def _verify_ticket_103_prerequisites(
    candidate: BossSynthesisCandidate,
    *,
    tickets: list[dict[str, Any]],
    financial: dict[str, Any],
    decisions: list[dict[str, Any]],
    product: dict[str, Any],
) -> dict[str, Any]:
    """Verify Ticket 103's real saved note/draft, decided choice, and current MCP facts."""
    ticket = next((row for row in tickets if row.get("id") == 103), None)
    if not ticket or ticket.get("type") != "price_override" \
            or str(ticket.get("status", "")).casefold() != "open" \
            or (ticket.get("sku"), ticket.get("size"), ticket.get("qty")) != (
                "CC-HOOD-NAVY", "M", 20
            ):
        raise RecoveryNotEligible("Ticket 103 must still be the same open 20-unit navy hoodie price override.")

    accounting = candidate.reports.get("accounting")
    customer_service = candidate.reports.get("customer_service")
    if (accounting is None or accounting.human_approval_required
            or customer_service is None or customer_service.human_approval_required):
        raise RecoveryNotEligible("Saved Accounting and Customer Service reports must not have an unresolved approval.")

    if any(str(row.get("status", "")).casefold() not in _TERMINAL_FINANCIAL_STATES
           for row in financial.get("ticket_payment_requests", [])
           + financial.get("ticket_purchase_requests", [])):
        raise RecoveryNotEligible("Ticket 103 has an unresolved payment or purchase request.")

    discount_decisions = [row for row in decisions
                          if row.get("ticket_id") == 103 and row.get("kind") == "discount"]
    decided = [row for row in discount_decisions
               if row.get("status") == "decided"
               and str(row.get("decided_by", "")).strip()
               and str(row.get("terms", "")).strip()
               and row.get("choice") in row.get("options", [])]
    if len(discount_decisions) != 1 or len(decided) != 1 \
            or decided[0].get("choice") != "Ask customer for a target unit price":
        raise RecoveryNotEligible(
            "Ticket 103 needs its single valid human discount decision to request the customer's target price."
        )
    if any(row.get("status") != "decided" for row in decisions if row.get("ticket_id") == 103):
        raise RecoveryNotEligible("Ticket 103 still has a pending human decision.")

    if product.get("ticket_id") != 103 or product.get("sku") != "CC-HOOD-NAVY" \
            or product.get("size") != "M" or product.get("requested_qty") != 20 \
            or product.get("available_qty") != 8 or product.get("shortfall_qty") != 12:
        raise RecoveryNotEligible("Current MCP stock facts no longer match the saved note and draft.")
    pricing = product.get("pricing") or {}
    if round(float(pricing.get("unit_cost", -1)) * 100) != 2_200 \
            or round(float(pricing.get("list_price", -1)) * 100) != 5_800:
        raise RecoveryNotEligible("Current MCP pricing no longer matches the saved Accounting analysis.")

    board_items = product.get("board_items")
    if not isinstance(board_items, list) or candidate.board_write_ids is None:
        raise RecoveryNotEligible("MCP did not return the persisted ticket-board evidence.")
    note_id = candidate.board_write_ids.get("accounting")
    draft_id = candidate.board_write_ids.get("customer_service")
    notes = [row for row in board_items if row.get("item_type") == "agent_note"
             and row.get("author") == "Accounting"]
    drafts = [row for row in board_items if row.get("item_type") == "customer_message_draft"
              and row.get("author") == "Customer Service"]
    note = next((row for row in notes if row.get("update_id") == note_id), None)
    draft = next((row for row in drafts if row.get("update_id") == draft_id), None)
    if note is None or draft is None or len(notes) != 1 or len(drafts) != 1:
        raise RecoveryNotEligible(
            "The failed run's exact Accounting note and Customer Service draft must each exist once on the board."
        )
    try:
        note_body = json.loads(str(note.get("body", "")))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RecoveryNotEligible("The saved Accounting note is not valid structured evidence.") from exc
    expected_note = {
        "kind": "price_override_resolution",
        "decision": "ask_customer_for_discount_target",
        "sku": "CC-HOOD-NAVY",
        "size": "M",
        "requested_qty": 20,
        "available_qty": 8,
        "shortfall_qty": 12,
        "unit_cost": 22.0,
        "list_price": 58.0,
        "offer_unit_price": None,
    }
    if note_body != expected_note:
        raise RecoveryNotEligible("Saved Accounting note does not match current ticket, stock, and pricing facts.")

    draft_text = str(draft.get("body", ""))
    draft_lower = draft_text.casefold()
    if not all((
        "target unit price" in draft_lower,
        "delivery date" in draft_lower,
        re.search(r"\b8\b.*\b20\b", draft_text, flags=re.DOTALL),
        re.search(r"\b12\s+short\b", draft_lower),
        draft.get("update_id") == draft_id,
    )) or any(row.get("sent_externally") is True for row in [draft]):
        raise RecoveryNotEligible(
            "Saved customer draft must ask for a target price, match stock facts, and remain unsent."
        )

    return {
        "ticket": {key: ticket.get(key) for key in
                   ("id", "type", "subject", "sku", "size", "qty", "status")},
        "discount_decision": decided[0],
        "product": {
            "sku": product["sku"], "size": product["size"],
            "requested_qty": product["requested_qty"],
            "available_qty": product["available_qty"],
            "shortfall_qty": product["shortfall_qty"],
            "unit_cost": pricing["unit_cost"], "list_price": pricing["list_price"],
        },
        "saved_board_items": {
            "accounting_note_update_id": note_id,
            "customer_message_draft_update_id": draft_id,
            "draft_sent_externally": False,
        },
        "financial_requests": financial.get("ticket_payment_requests", [])
        + financial.get("ticket_purchase_requests", []),
    }


def recovery_candidate_status(ticket_id: int) -> dict[str, Any]:
    """Read audit evidence only; live MCP prerequisites are rechecked on POST."""
    try:
        candidate = find_boss_synthesis_candidate(ticket_id)
    except RecoveryNotEligible as exc:
        reason = str(exc)
        return {
            "ticket_id": ticket_id,
            "eligible": False,
            "in_progress": reason.startswith("A Boss-synthesis recovery is already queued or running"),
            "reason": reason,
        }
    return {
        "ticket_id": ticket_id,
        "eligible": True,
        "in_progress": False,
        "reason": "Saved specialist evidence is complete; current MCP prerequisites will be rechecked before recovery.",
        "failed_workflow_id": candidate.workflow_id,
        "saved_specialists": list(candidate.reports),
    }


def _complete_report_is_truthful(report: AgentReport) -> bool:
    """Require Boss's complete synthesis to keep the no-delivery distinction explicit."""
    if report.status != "complete":
        return True
    text = " ".join(
        [report.summary, *report.findings, *report.decisions, *report.next_steps]
    ).casefold()
    affirmative_delivery = re.search(
        r"\b(?:was|is|has been|have been)\s+(?:delivered|received|shipped|restocked)\b",
        text,
    )
    explicit_no_delivery = re.search(
        r"\bno (?:item|goods|delivery|shipment)\b|\bnot (?:been )?(?:delivered|received)\b",
        text,
    )
    return affirmative_delivery is None and explicit_no_delivery is not None


def _price_override_report_is_truthful(report: AgentReport) -> bool:
    """Keep recovered Ticket 103 synthesis faithful to the saved decision and draft."""
    if report.status != "complete":
        return True
    text = " ".join([report.summary, *report.findings, *report.decisions, *report.next_steps]).casefold()
    asks_for_target = "target unit price" in text and any(word in text for word in ("ask", "request"))
    identifies_unsent_draft = "draft" in text and any(
        phrase in text for phrase in ("not sent", "unsent", "has not been sent")
    )
    unsupported_discount_claim = re.search(
        r"\b(?:discount|offer)\s+(?:was|is|has been)\s+approved\b|\b20\s+(?:hoodies\s+)?(?:are|were)\s+(?:in stock|available)\b",
        text,
    )
    return asks_for_target and identifies_unsent_draft and unsupported_discount_claim is None


async def load_verified_current_evidence(
    candidate: BossSynthesisCandidate,
    recovery_workflow_id: str,
    *,
    tool_caller: Callable[..., Any] = call_mcp_tool,
) -> dict[str, Any]:
    """Read authoritative MCP facts and fail closed before any model request."""

    async def read_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await tool_caller(name, arguments or {}, workflow_id=recovery_workflow_id)

    reads = [
        read_tool("list_tickets"),
        read_tool("get_ticket_financial_context", {"ticket_id": candidate.ticket_id}),
        read_tool("list_decisions", {"ticket_id": candidate.ticket_id}),
        read_tool("get_ticket_product_context", {"ticket_id": candidate.ticket_id}),
    ]
    if candidate.ticket_id == 103:
        reads.append(read_tool("get_ticket_board_items", {"ticket_id": candidate.ticket_id}))
    results = await asyncio.gather(*reads)
    tickets, financial, decisions, product = results[:4]
    if candidate.ticket_id == 103:
        product = {**product, "board_items": results[4]}
    return verify_recovery_prerequisites(
        candidate,
        tickets=tickets,
        financial=financial,
        decisions=decisions,
        product=product,
    )


async def resume_failed_boss_synthesis(
    ticket_id: int,
    failed_workflow_id: str,
    recovery_workflow_id: str,
    *,
    model: Any | None = None,
    tool_caller: Callable[..., Any] = call_mcp_tool,
    history: list[dict[str, Any]] | None = None,
) -> WorkflowReport:
    """Run Boss alone from audited peer outputs after MCP revalidates current state."""
    candidate = find_boss_synthesis_candidate(
        ticket_id,
        failed_workflow_id,
        history=history,
        allow_recovery_workflow_id=recovery_workflow_id,
    )

    current = await load_verified_current_evidence(
        candidate,
        recovery_workflow_id,
        tool_caller=tool_caller,
    )

    compact_context = {
        **candidate.compact_evidence(),
        "current_mcp_evidence": current,
        "preflight_note": "Every mandatory business prerequisite was independently checked through current MCP reads.",
    }
    prompt = (
        f"Synthesize the saved specialist outputs for Ticket {ticket_id} against current MCP facts. "
        "Return a brief complete report only if the preflight evidence satisfies the ticket's "
        "requirements; otherwise report blocked and name the missing fact. Do not repeat work, "
        "make tool calls, or claim unsaved actions.\nEVIDENCE="
        + json.dumps(compact_context, ensure_ascii=False, separators=(",", ":"))
    )

    append_audit_record({
        "event": "workflow_started",
        "workflow": "recover_boss_synthesis",
        "workflow_id": recovery_workflow_id,
        "recovery_of": failed_workflow_id,
        "ticket_id": ticket_id,
        "database_reset": False,
        "max_agent_calls": 1,
        "max_workflow_tokens": (
            PRICE_OVERRIDE_RECOVERY_MAX_TOKENS if ticket_id == 103 else RECOVERY_MAX_TOKENS
        ),
    })
    append_audit_record({
        "event": "boss_synthesis_recovery_preflight",
        "workflow_id": recovery_workflow_id,
        "recovery_of": failed_workflow_id,
        "ticket_id": ticket_id,
        "saved_specialists": list(candidate.reports),
        "current_evidence": current,
    })

    selected_model = model if model is not None else create_portkey_model()
    boss = Agent(
        selected_model,
        name="campus_customs_boss_recovery",
        output_type=AgentReport,
        instructions=RECOVERY_INSTRUCTIONS,
        # Intentionally no MCP toolset or peer-delegation tool in this path.
    )
    usage = RunUsage()
    append_audit_record({
        "event": "agent_run_started",
        "agent": "boss",
        "workflow_id": recovery_workflow_id,
        "recovery_of": failed_workflow_id,
        "parent_agent": None,
        "delegation_depth": 0,
        "call_number": 1,
        "ticket_id": ticket_id,
        "task": "Synthesize saved specialist results against current MCP evidence; no delegation or tools.",
    })
    try:
        result = await boss.run(
            prompt,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=(
                    PRICE_OVERRIDE_RECOVERY_MAX_REQUESTS
                    if ticket_id == 103 else RECOVERY_MAX_REQUESTS
                ),
                tool_calls_limit=None,
                output_tokens_limit=(
                    PRICE_OVERRIDE_RECOVERY_MAX_OUTPUT_TOKENS
                    if ticket_id == 103 else RECOVERY_MAX_OUTPUT_TOKENS
                ),
                total_tokens_limit=(
                    PRICE_OVERRIDE_RECOVERY_MAX_TOKENS
                    if ticket_id == 103 else RECOVERY_MAX_TOKENS
                ),
            ),
        )
    except Exception as exc:
        used = usage
        append_audit_record({
            "event": "agent_run_failed",
            "agent": "boss",
            "workflow_id": recovery_workflow_id,
            "recovery_of": failed_workflow_id,
            "parent_agent": None,
            "delegation_depth": 0,
            "call_number": 1,
            "ticket_id": ticket_id,
            "error": f"{type(exc).__name__}: {exc}",
            "usage": {"requests": used.requests, "tool_calls": used.tool_calls,
                      "total_tokens": int(used.total_tokens or 0)},
        })
        append_audit_record({
            "event": "workflow_failed",
            "workflow": "recover_boss_synthesis",
            "workflow_id": recovery_workflow_id,
            "recovery_of": failed_workflow_id,
            "ticket_id": ticket_id,
            "error": f"{type(exc).__name__}: {exc}",
            "agent_calls": 1,
            "tokens_used": int(used.total_tokens or 0),
        })
        raise
    report = result.output
    used = result.usage
    used_tokens = int(used.total_tokens or 0)
    append_audit_record({
        "event": "agent_run_completed",
        "agent": "boss",
        "workflow_id": recovery_workflow_id,
        "recovery_of": failed_workflow_id,
        "parent_agent": None,
        "delegation_depth": 0,
        "call_number": 1,
        "ticket_id": ticket_id,
        "status": report.status,
        "output": report.model_dump(mode="json"),
        "usage": {"requests": used.requests, "tool_calls": used.tool_calls,
                  "total_tokens": used_tokens},
    })
    is_truthful = (
        _complete_report_is_truthful(report)
        if ticket_id == 101 else _price_override_report_is_truthful(report)
    )
    if report.status != "complete" or report.human_approval_required or not is_truthful:
        reason = (
            "Boss did not confirm a complete synthesis"
            if report.status != "complete"
            else "Boss still reports an unresolved human approval"
            if report.human_approval_required
            else (
                "Boss's synthesis must explicitly avoid claiming receipt or delivery"
                if ticket_id == 101 else
                "Boss's synthesis does not accurately describe the saved unsent customer draft"
            )
        )
        append_audit_record({
            "event": "workflow_failed",
            "workflow": "recover_boss_synthesis",
            "workflow_id": recovery_workflow_id,
            "recovery_of": failed_workflow_id,
            "ticket_id": ticket_id,
            "error": reason,
            "agent_calls": 1,
            "tokens_used": used_tokens,
        })
        raise RecoveryNotEligible(f"{reason}; the final-disposition gate was not run.")
    append_audit_record({
        "event": "workflow_completed",
        "workflow": "recover_boss_synthesis",
        "workflow_id": recovery_workflow_id,
        "recovery_of": failed_workflow_id,
        "ticket_id": ticket_id,
        "status": report.status,
        "summary": report.summary,
        "agent_calls": 1,
        "tokens_used": used_tokens,
        "saved_specialists_reused": list(candidate.reports),
    })
    return WorkflowReport(
        status=report.status,
        summary=report.summary,
        boss_report=report,
        agent_calls=1,
        tokens_used=used_tokens,
        audit_file="output/audit_trail.json",
    )
