"""Campus Customs MCP tools backed only by the HW5 working database."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import wraps
import fcntl
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Callable, Iterator, Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(os.environ.get(
    "CAMPUS_CUSTOMS_DB_PATH", str(PROJECT_ROOT / "data" / "campus_customs_new.db")
)).expanduser().resolve()
AUDIT_PATH = Path(os.environ.get(
    "CAMPUS_CUSTOMS_AUDIT_PATH", str(PROJECT_ROOT / "output" / "audit_trail.json")
)).expanduser().resolve()
DECISIONS_PATH = Path(os.environ.get(
    "CAMPUS_CUSTOMS_DECISIONS_PATH", str(PROJECT_ROOT / "output" / "human_decisions.json")
)).expanduser().resolve()

mcp = FastMCP("Campus Customs Operations")


def _append_audit_record(record: dict[str, Any]) -> None:
    """Append one item to a valid JSON array without replacing prior records."""
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".lock")
    safe_record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    encoded_record = json.dumps(safe_record, ensure_ascii=False, separators=(",", ":"))
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if not AUDIT_PATH.exists() or AUDIT_PATH.stat().st_size == 0:
            AUDIT_PATH.write_text("[]\n", encoding="utf-8")
        with AUDIT_PATH.open("r+b") as audit_file:
            content = audit_file.read()
            stripped = content.rstrip()
            if not stripped.endswith(b"]"):
                raise ValueError("Audit trail is not a JSON array; refusing to overwrite it")
            prefix = stripped[:-1].rstrip()
            addition = (b"" if prefix == b"[" else b",") + encoded_record.encode("utf-8")
            audit_file.seek(len(prefix))
            audit_file.write(addition + b"]\n")
            audit_file.truncate()
            audit_file.flush()
            import os

            os.fsync(audit_file.fileno())
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked_decision_store(*, write: bool = False) -> Iterator[list[dict[str, Any]]]:
    """Read or atomically update the external decision JSON under a sidecar lock."""
    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = DECISIONS_PATH.with_suffix(DECISIONS_PATH.suffix + ".lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        if DECISIONS_PATH.exists():
            try:
                records = json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Decision store is unreadable; refusing to replace it: {exc}") from exc
            if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
                raise ValueError("Decision store must contain a JSON array of decision objects")
        else:
            records = []
        original_records = json.loads(json.dumps(records, ensure_ascii=False))
        try:
            yield records
            if write and records != original_records:
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix="human_decisions_", suffix=".json", dir=DECISIONS_PATH.parent
                )
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as decision_file:
                        json.dump(records, decision_file, ensure_ascii=False, indent=2)
                        decision_file.write("\n")
                        decision_file.flush()
                        os.fsync(decision_file.fileno())
                    os.replace(temporary_name, DECISIONS_PATH)
                finally:
                    if os.path.exists(temporary_name):
                        os.unlink(temporary_name)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _active_requesting_agent() -> str:
    """Infer the active agent role from the current workflow's append-only events."""
    workflow_id = os.environ.get("CAMPUS_CUSTOMS_WORKFLOW_ID")
    if not workflow_id or not AUDIT_PATH.is_file():
        return "agent"
    lock_path = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            history = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finished = {
        row.get("call_number") for row in history
        if row.get("workflow_id") == workflow_id
        and row.get("event") in {"agent_run_completed", "agent_run_failed"}
    }
    for row in reversed(history):
        if (row.get("workflow_id") == workflow_id
                and row.get("event") == "agent_run_started"
                and row.get("call_number") not in finished
                and isinstance(row.get("agent"), str)):
            return row["agent"]
    return "agent"


def _record_decision_request(
    ticket_id: int,
    kind: Literal["fulfillment", "discount", "other"],
    question: str,
    recommendation: str,
    options: list[str],
) -> dict[str, Any]:
    question, recommendation = question.strip(), recommendation.strip()
    if not question or len(question) > 500:
        raise ValueError("question must contain 1 to 500 characters")
    if not recommendation or len(recommendation) > 1000:
        raise ValueError("recommendation must contain 1 to 1000 characters")
    if not 2 <= len(options) <= 8:
        raise ValueError("options must contain 2 to 8 choices")
    normalized_options = [option.strip() for option in options]
    if any(not option or len(option) > 160 for option in normalized_options):
        raise ValueError("each option must contain 1 to 160 characters")
    if len({option.casefold() for option in normalized_options}) != len(normalized_options):
        raise ValueError("options must be distinct")

    with _open_database() as connection:
        ticket = _get_ticket(connection, ticket_id)
        if str(ticket["status"]).casefold() != "open":
            raise ValueError("Human decisions may only be requested for an open ticket")
        if kind == "fulfillment" and ticket["type"] != "customer_order":
            raise ValueError("fulfillment decisions are only valid for customer orders")
        if kind == "discount" and ticket["type"] != "price_override":
            raise ValueError("discount decisions are only valid for price override tickets")

    workflow_id = os.environ.get("CAMPUS_CUSTOMS_WORKFLOW_ID") or None
    with _locked_decision_store(write=True) as decisions:
        matching = [row for row in decisions
                    if row.get("ticket_id") == ticket_id
                    and row.get("kind") == kind
                    and row.get("status") in {"pending", "decided"}]
        pending = [row for row in matching if row.get("status") == "pending"]
        decided = [row for row in matching if row.get("status") == "decided"]
        existing = max(pending, key=lambda row: row.get("created_at", ""), default=None)
        if existing is None:
            existing = max(decided, key=lambda row: row.get("created_at", ""), default=None)
        if existing is not None:
            # Treat retries as idempotent even after a human has decided.
            # Return the recorded choice; never silently replace or duplicate it.
            return {**existing, "reused_existing": True}
        decision = {
            "decision_id": str(uuid4()),
            "ticket_id": ticket_id,
            "kind": kind,
            "question": question,
            "recommendation": recommendation,
            "options": normalized_options,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requested_by": _active_requesting_agent(),
            "workflow_id": workflow_id,
            "decided_by": None,
            "choice": None,
            "terms": None,
            "decided_at": None,
        }
        decisions.append(decision)
    return {**decision, "reused_existing": False}


def _record_human_decision(
    decision_id: str, decided_by: str, choice: str, terms: str
) -> dict[str, Any]:
    decided_by, choice, terms = decided_by.strip(), choice.strip(), terms.strip()
    if not decided_by or len(decided_by) > 80:
        raise ValueError("decided_by must contain 1 to 80 characters")
    if not choice or len(choice) > 160:
        raise ValueError("choice must contain 1 to 160 characters")
    if not terms or len(terms) > 2000:
        raise ValueError("terms must contain 1 to 2000 characters")

    decided_at = datetime.now(timezone.utc).isoformat()
    with _locked_decision_store(write=True) as decisions:
        decision = next((row for row in decisions
                         if row.get("decision_id") == decision_id), None)
        if decision is None:
            raise ValueError("Decision ID was not found")
        if decision.get("status") != "pending":
            raise ValueError("Decision has already been decided")
        if choice not in decision.get("options", []):
            raise ValueError("choice must match one of the recorded options")
        decision.update({
            "status": "decided",
            "decided_by": decided_by,
            "choice": choice,
            "terms": terms,
            "decided_at": decided_at,
        })
        recorded = dict(decision)
    _append_audit_record({"event": "human_decision", **recorded})
    return recorded


def _list_decision_records(
    ticket_id: int | None = None,
    status: Literal["pending", "decided"] | None = None,
) -> list[dict[str, Any]]:
    if status is not None and status not in {"pending", "decided"}:
        raise ValueError("status must be 'pending' or 'decided'")
    with _locked_decision_store() as decisions:
        return [
            dict(item) for item in decisions
            if (ticket_id is None or item.get("ticket_id") == ticket_id)
            and (status is None or item.get("status") == status)
        ]


def _latest_ticket_workflow_completion(ticket_id: int) -> tuple[bool, dict[str, Any]]:
    """Require the latest recorded ticket workflow to have reached workflow_completed."""
    if not AUDIT_PATH.is_file():
        return False, {"reason": "No workflow audit history is available"}
    lock_path = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            try:
                history = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Workflow audit history is unreadable: {exc}") from exc
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    if not isinstance(history, list):
        raise ValueError("Workflow audit history must be a JSON array")
    initiations = [
        (index, row) for index, row in enumerate(history)
        if isinstance(row, dict) and row.get("event") in {
            "workflow_started", "api_ticket_run_requested", "api_ticket_continuation_requested"
        }
        and row.get("ticket_id") == ticket_id
    ]
    if not initiations:
        return False, {"reason": "No completed workflow is recorded for this ticket"}
    initiation_index, latest = initiations[-1]
    workflow_id = latest.get("workflow_id")
    if latest.get("event") != "workflow_started":
        started = next((
            (index, row) for index, row in enumerate(history[initiation_index + 1:], initiation_index + 1)
            if isinstance(row, dict) and row.get("event") == "workflow_started"
            and row.get("workflow_id") == workflow_id and row.get("ticket_id") == ticket_id
        ), None)
        if started is None:
            return False, {
                "workflow_id": workflow_id,
                "workflow_requested_at": latest.get("timestamp"),
                "reason": "The latest ticket workflow is queued or has not started",
            }
        start_index, latest = started
    else:
        start_index = initiation_index
    workflow_id = latest.get("workflow_id")
    completed = any(
        isinstance(row, dict) and row.get("event") == "workflow_completed"
        and row.get("workflow_id") == workflow_id and row.get("ticket_id") == ticket_id
        for row in history[start_index + 1:]
    )
    return completed, {
        "workflow_id": workflow_id,
        "workflow_started_at": latest.get("timestamp"),
        "reason": None if completed else "The latest ticket workflow has not completed",
    }


def _evaluate_final_disposition(
    connection: sqlite3.Connection,
    ticket: sqlite3.Row,
    decisions: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    """One documented gate for a truthful, non-stock-consuming ticket resolution.

    Customer orders require a completed latest workflow, every payment/purchase
    proposal to be human-decided, any linked invoice already paid, and (when stock
    is short) an explicit human fulfillment decision with terms. Resolution records
    that the blocker/outcome was handled; it never claims delivery or changes stock.
    Existing rent and price-override evidence checks remain in force.
    """
    outstanding: list[str] = []
    evidence: dict[str, Any] = {"ticket_type": ticket["type"]}
    completed, workflow_evidence = _latest_ticket_workflow_completion(int(ticket["id"]))
    evidence["latest_workflow"] = workflow_evidence
    if not completed:
        outstanding.append(str(workflow_evidence["reason"]))

    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()}
    financial_requests: list[dict[str, Any]] = []
    for table in ("payment_requests", "purchase_requests"):
        if table in tables:
            financial_requests.extend(dict(row) | {"request_type": table}
                                      for row in connection.execute(
                f"SELECT * FROM {table} WHERE ticket_id = ? ORDER BY id", (ticket["id"],)
            ).fetchall())
    undecided_requests = [
        {"request_type": row["request_type"], "id": row["id"], "status": row["status"]}
        for row in financial_requests
        if str(row.get("status", "")).casefold() not in {"approved", "processed", "rejected"}
    ]
    evidence["financial_requests"] = financial_requests
    if undecided_requests:
        outstanding.append(f"Financial requests still need a human decision: {undecided_requests}")

    if ticket["type"] == "customer_order":
        sku, size, quantity = ticket["sku"], ticket["size"], ticket["qty"]
        if not sku or not size or quantity is None or int(quantity) <= 0:
            outstanding.append("Customer order is missing its SKU, size, or positive quantity")
            return outstanding, evidence
        stock = connection.execute(
            "SELECT qty FROM inventory WHERE sku = ? AND size = ?", (sku, size)
        ).fetchone()
        available = int(stock["qty"]) if stock else 0
        shortfall = max(int(quantity) - available, 0)
        evidence["inventory_check"] = {
            "sku": sku, "size": size, "requested_qty": int(quantity),
            "available_qty": available, "shortfall_qty": shortfall,
            "inventory_consumed": False,
        }
        if ticket["invoice_id"] is not None:
            invoice = connection.execute(
                "SELECT id, status FROM invoices WHERE id = ?", (ticket["invoice_id"],)
            ).fetchone()
            evidence["linked_invoice"] = dict(invoice) if invoice else {
                "id": ticket["invoice_id"], "status": "missing"
            }
            if invoice is None or str(invoice["status"]).casefold() != "paid":
                outstanding.append(
                    f"Linked invoice {ticket['invoice_id']} is not recorded as paid"
                )
        if shortfall > 0:
            fulfillment_requests = [
                item for item in decisions
                if item.get("ticket_id") == int(ticket["id"])
                and item.get("kind") == "fulfillment"
            ]
            fulfillment = next((item for item in reversed(decisions)
                                if item.get("ticket_id") == int(ticket["id"])
                                and item.get("kind") == "fulfillment"
                                and item.get("status") == "decided"
                                and item.get("decided_by") and item.get("terms")), None)
            evidence["fulfillment_decision"] = dict(fulfillment) if fulfillment else None
            if fulfillment is None:
                pending_fulfillment = next((item for item in reversed(fulfillment_requests)
                                            if item.get("status") == "pending"), None)
                if pending_fulfillment:
                    evidence["pending_fulfillment_decision"] = dict(pending_fulfillment)
                    outstanding.append(
                        f"Human fulfillment decision {pending_fulfillment['decision_id']} is pending "
                        f"for shortfall {shortfall} ({sku}, size {size}); awaiting a human choice and terms"
                    )
                else:
                    outstanding.append(
                        f"Human fulfillment decision with terms is required for shortfall {shortfall} "
                        f"({sku}, size {size}); no goods receipt is evidenced"
                    )
    elif ticket["type"] == "rent_notice":
        lease_id = ticket["lease_id"]
        if lease_id is None:
            outstanding.append("Rent notice has no linked lease")
        else:
            lease = connection.execute(
                "SELECT id, next_due FROM leases WHERE id = ?", (lease_id,)
            ).fetchone()
            paid_rent = connection.execute(
                """SELECT pr.id AS request_id, p.id AS payment_id, p.amount, p.paid_at
                   FROM payment_requests AS pr JOIN payments AS p
                     ON p.kind = pr.kind AND p.ref_id = pr.ref_id
                    AND p.amount = pr.amount AND p.account = pr.account
                   WHERE pr.ticket_id = ? AND pr.kind = 'rent' AND pr.ref_id = ?
                     AND pr.status = 'processed' ORDER BY p.id DESC LIMIT 1""",
                (ticket["id"], lease_id),
            ).fetchone() if "payment_requests" in tables else None
            if lease is None or paid_rent is None:
                outstanding.append("Linked rent payment is not recorded as processed")
            else:
                evidence["rent_payment"] = dict(paid_rent)
    elif ticket["type"] == "price_override":
        # Keep the existing structured pricing and draft evidence check below.
        sku, size, quantity = ticket["sku"], ticket["size"], ticket["qty"]
        product = connection.execute(
            """SELECT i.qty AS available_qty, p.unit_cost, p.list_price
               FROM inventory i JOIN pricing p ON p.sku = i.sku
               WHERE i.sku = ? AND i.size = ?""", (sku, size)
        ).fetchone() if sku and size else None
        if product is None or quantity is None:
            outstanding.append("Price override lacks matching inventory/pricing facts")
        else:
            decision_row = None
            for item in connection.execute(
                """SELECT id, body FROM ticket_updates WHERE ticket_id = ?
                   AND item_type = 'agent_note' ORDER BY id DESC""", (ticket["id"],)
            ).fetchall():
                try:
                    candidate = json.loads(item["body"])
                    if (candidate.get("kind") == "price_override_resolution"
                            and candidate.get("decision") == "ask_customer_for_discount_target"
                            and candidate.get("sku") == sku and candidate.get("size") == size
                            and candidate.get("requested_qty") == int(quantity)
                            and candidate.get("available_qty") == int(product["available_qty"])
                            and candidate.get("shortfall_qty") == max(int(quantity)-int(product["available_qty"]), 0)
                            and round(float(candidate.get("unit_cost"))*100) == round(float(product["unit_cost"])*100)
                            and round(float(candidate.get("list_price"))*100) == round(float(product["list_price"])*100)
                            and candidate.get("offer_unit_price") is None):
                        decision_row = item
                        break
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            draft = connection.execute(
                """SELECT id FROM ticket_updates WHERE ticket_id = ?
                   AND item_type = 'customer_message_draft' ORDER BY id DESC LIMIT 1""",
                (ticket["id"],),
            ).fetchone()
            if decision_row is None or draft is None:
                outstanding.append(
                    "Price override needs a fact-matched request-for-target-price decision "
                    "and a saved customer message draft"
                )
            else:
                evidence["pricing_disposition_update_id"] = decision_row["id"]
                evidence["customer_draft_update_id"] = draft["id"]
    else:
        outstanding.append(f"Ticket type {ticket['type']!r} has no verified resolution rule")
    return outstanding, evidence


def _registered_tool(
    *, read_only: bool, idempotent: bool = False
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a FastMCP tool and audit its actual inputs and result."""
    annotations = ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=not read_only,
        idempotentHint=read_only or idempotent,
        openWorldHint=False,
    )

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def audited(*args: Any, **kwargs: Any) -> Any:
            inputs = kwargs if kwargs else {"args": list(args)}
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                _append_audit_record({
                    "event": "mcp_tool",
                    "workflow_id": os.environ.get("CAMPUS_CUSTOMS_WORKFLOW_ID") or None,
                    "tool": function.__name__,
                    "inputs": inputs,
                    "outcome": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                raise
            _append_audit_record({
                "event": "mcp_tool",
                "workflow_id": os.environ.get("CAMPUS_CUSTOMS_WORKFLOW_ID") or None,
                "tool": function.__name__,
                "inputs": inputs,
                "outcome": "success",
                "result": result,
            })
            return result

        return mcp.tool(annotations=annotations)(audited)

    return decorate


@contextmanager
def _open_database(*, writable: bool = False) -> Iterator[sqlite3.Connection]:
    """Open the working copy; reads default to SQLite read-only mode."""
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"Working database not found: {DATABASE_PATH}")
    if writable:
        connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    else:
        connection = sqlite3.connect(f"{DATABASE_PATH.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def _ensure_extension_tables(connection: sqlite3.Connection) -> None:
    """Create app-owned board and approval-request tables in the working DB."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS ticket_updates (
            id INTEGER PRIMARY KEY,
            ticket_id INTEGER NOT NULL,
            item_type TEXT NOT NULL CHECK (item_type IN
                ('agent_note', 'customer_message_draft', 'vendor_message_draft')),
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY,
            ticket_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('invoice', 'rent')),
            ref_id INTEGER NOT NULL,
            amount REAL NOT NULL CHECK (amount > 0),
            account TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending_approval', 'rejected', 'approved', 'processed')),
            requested_at TEXT NOT NULL,
            approved_by TEXT,
            UNIQUE (ticket_id, kind, ref_id),
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS purchase_requests (
            id INTEGER PRIMARY KEY,
            ticket_id INTEGER NOT NULL,
            sku TEXT NOT NULL,
            size TEXT NOT NULL,
            qty INTEGER NOT NULL CHECK (qty > 0),
            vendor_id INTEGER NOT NULL,
            unit_cost REAL NOT NULL CHECK (unit_cost > 0),
            total_amount REAL NOT NULL CHECK (total_amount > 0),
            account TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending_approval', 'rejected', 'processed')),
            requested_at TEXT NOT NULL,
            approved_by TEXT,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        )"""
    )


def _get_ticket(connection: sqlite3.Connection, ticket_id: int) -> sqlite3.Row:
    ticket = connection.execute(
        "SELECT * FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} was not found")
    return ticket


def _shop_date(connection: sqlite3.Connection) -> date:
    row = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
    if row is None:
        raise ValueError("The desk table has no shop date")
    return date.fromisoformat(row["date_today"])


@_registered_tool(read_only=True)
def get_ticket_product_context(ticket_id: int) -> dict[str, Any]:
    """Look up a ticket's requested item's stock, shortfall, and pricing."""
    with _open_database() as connection:
        ticket = _get_ticket(connection, ticket_id)
        sku, size = ticket["sku"], ticket["size"]
        if not sku or not size:
            raise ValueError(f"Ticket {ticket_id} does not specify both SKU and size")
        stock = connection.execute(
            "SELECT sku, name, size, qty, location FROM inventory WHERE sku = ? AND size = ?",
            (sku, size),
        ).fetchone()
        pricing = connection.execute(
            "SELECT sku, unit_cost, list_price FROM pricing WHERE sku = ?", (sku,)
        ).fetchone()
        requested_qty = ticket["qty"]
        available_qty = stock["qty"] if stock is not None else None
        shortfall_qty = (
            max(requested_qty - available_qty, 0)
            if requested_qty is not None and available_qty is not None else None
        )
        if stock is None:
            stock_status = "no_matching_inventory_record"
        elif available_qty == 0:
            stock_status = "out_of_stock"
        elif shortfall_qty:
            stock_status = "shortfall"
        else:
            stock_status = "sufficient_stock"
        return {
            "ticket_id": ticket["id"], "subject": ticket["subject"],
            "sku": sku, "size": size, "requested_qty": requested_qty,
            "inventory": dict(stock) if stock is not None else None,
            "available_qty": available_qty, "shortfall_qty": shortfall_qty,
            "stock_status": stock_status,
            "pricing": dict(pricing) if pricing is not None else None,
        }


@_registered_tool(read_only=True)
def get_ticket_supplier_context(ticket_id: int) -> dict[str, Any]:
    """Check a ticket-linked vendor's lead time and outstanding invoices."""
    with _open_database() as connection:
        ticket = _get_ticket(connection, ticket_id)
        invoice_id = ticket["invoice_id"]
        if invoice_id is None:
            raise ValueError(f"Ticket {ticket_id} has no linked vendor invoice")
        linked = connection.execute(
            """SELECT i.id AS invoice_id, i.vendor_id, i.amount, i.due_date,
                      i.status AS invoice_status, i.description,
                      v.name AS vendor_name, v.specialty, v.lead_days
               FROM invoices AS i JOIN vendors AS v ON v.id = i.vendor_id
               WHERE i.id = ?""", (invoice_id,),
        ).fetchone()
        if linked is None:
            raise ValueError(f"Invoice {invoice_id} linked to ticket {ticket_id} was not found")
        today = _shop_date(connection)
        unpaid_rows = connection.execute(
            """SELECT id, amount, due_date, status, description FROM invoices
               WHERE vendor_id = ? AND lower(status) <> 'paid' ORDER BY due_date, id""",
            (linked["vendor_id"],),
        ).fetchall()
        outstanding = []
        for row in unpaid_rows:
            invoice = dict(row)
            invoice["overdue"] = date.fromisoformat(invoice["due_date"]) < today
            outstanding.append(invoice)
        return {
            "ticket_id": ticket["id"], "shop_date": today.isoformat(),
            "linked_invoice": dict(linked),
            "linked_invoice_overdue": (
                date.fromisoformat(linked["due_date"]) < today
                and linked["invoice_status"].casefold() != "paid"
            ),
            "vendor_outstanding_invoices": outstanding,
            "invoice_allows_new_shipment": len(outstanding) == 0,
        }


@_registered_tool(read_only=True)
def get_ticket_rent_context(ticket_id: int) -> dict[str, Any]:
    """Retrieve the linked lease and rent timing using the shop's business date."""
    with _open_database() as connection:
        ticket = _get_ticket(connection, ticket_id)
        if ticket["lease_id"] is None:
            raise ValueError(f"Ticket {ticket_id} has no linked lease")
        row = connection.execute(
            """SELECT id AS lease_id, space_name, landlord, monthly_rent, next_due, notes
               FROM leases WHERE id = ?""", (ticket["lease_id"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Lease {ticket['lease_id']} linked to ticket {ticket_id} was not found")
        today = _shop_date(connection)
        next_due = date.fromisoformat(row["next_due"])
        return {
            "ticket_id": ticket["id"], "subject": ticket["subject"],
            "shop_date": today.isoformat(), "lease": dict(row),
            "days_until_due": (next_due - today).days, "overdue": next_due < today,
        }


@_registered_tool(read_only=True)
def list_open_tickets() -> list[dict[str, Any]]:
    """Return open ticket-board entries for the Boss to triage."""
    with _open_database() as connection:
        rows = connection.execute(
            """SELECT id, type, requester, subject, sku, size, qty, lease_id,
                      invoice_id, status, notes, created_at
               FROM tickets WHERE lower(status) = 'open' ORDER BY id"""
        ).fetchall()
        return [dict(row) for row in rows]


@_registered_tool(read_only=True)
def list_tickets() -> list[dict[str, Any]]:
    """Return all tickets with current status for the dashboard."""
    with _open_database() as connection:
        rows = connection.execute(
            """SELECT id, type, requester, subject, sku, size, qty, lease_id,
                      invoice_id, status, notes, created_at
               FROM tickets ORDER BY id"""
        ).fetchall()
        return [dict(row) for row in rows]


@_registered_tool(read_only=True)
def get_cash_balance(account_name: str = "checking") -> dict[str, Any]:
    """Return a cash account's current balance and recorded balance date."""
    with _open_database() as connection:
        row = connection.execute(
            "SELECT name, balance, date FROM cash_accounts WHERE name = ?",
            (account_name.strip(),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Cash account {account_name!r} was not found")
        return dict(row)


@_registered_tool(read_only=True)
def list_pending_approval_requests() -> dict[str, list[dict[str, Any]]]:
    """List payment and purchase proposals that still need human approval."""
    with _open_database() as connection:
        tables = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()}
        payments: list[dict[str, Any]] = []
        purchases: list[dict[str, Any]] = []
        if "payment_requests" in tables:
            payments = [dict(row) for row in connection.execute(
                """SELECT p.id AS request_id, p.ticket_id, p.kind, p.ref_id,
                          p.amount, p.account, p.status, p.requested_at,
                          t.subject AS ticket_subject
                   FROM payment_requests AS p JOIN tickets AS t ON t.id = p.ticket_id
                   WHERE p.status = 'pending_approval' ORDER BY p.id"""
            ).fetchall()]
        if "purchase_requests" in tables:
            purchases = [dict(row) for row in connection.execute(
                """SELECT p.id AS request_id, p.ticket_id, p.sku, p.size, p.qty,
                          p.vendor_id, v.name AS vendor_name, p.unit_cost,
                          p.total_amount AS amount, p.account, p.status, p.requested_at,
                          t.subject AS ticket_subject
                   FROM purchase_requests AS p
                   JOIN tickets AS t ON t.id = p.ticket_id
                   JOIN vendors AS v ON v.id = p.vendor_id
                   WHERE p.status = 'pending_approval' ORDER BY p.id"""
            ).fetchall()]
        return {"payments": payments, "purchases": purchases}


@_registered_tool(read_only=False, idempotent=True)
def request_human_decision(
    ticket_id: int,
    kind: Literal["fulfillment", "discount", "other"],
    question: str,
    recommendation: str,
    options: list[str],
) -> dict[str, Any]:
    """Queue once per ticket/kind, reusing existing pending or decided choices."""
    return _record_decision_request(ticket_id, kind, question, recommendation, options)


@_registered_tool(read_only=True)
def list_decisions(
    ticket_id: int | None = None,
    status: Literal["pending", "decided"] | None = None,
) -> list[dict[str, Any]]:
    """Read externally stored human decisions, optionally filtered by ticket/status."""
    return _list_decision_records(ticket_id, status)


@_registered_tool(read_only=True)
def get_ticket_financial_context(ticket_id: int) -> dict[str, Any]:
    """Read account balances, recorded payments, and pending approval requests."""
    with _open_database() as connection:
        ticket = _get_ticket(connection, ticket_id)
        accounts = [dict(row) for row in connection.execute(
            "SELECT name, balance, date FROM cash_accounts ORDER BY name"
        ).fetchall()]
        payments = [dict(row) for row in connection.execute(
            """SELECT id, kind, ref_id, amount, account, paid_at, approved_by
               FROM payments ORDER BY id"""
        ).fetchall()]
        # Extension data is optional until a write tool has initialized it.
        tables = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()}
        pending = []
        if "payment_requests" in tables:
            pending = [dict(row) for row in connection.execute(
                """SELECT id, kind, ref_id, amount, account, status, requested_at
                   FROM payment_requests WHERE ticket_id = ? ORDER BY id""", (ticket_id,)
            ).fetchall()]
        pending_purchases = []
        if "purchase_requests" in tables:
            pending_purchases = [dict(row) for row in connection.execute(
                """SELECT id, sku, size, qty, vendor_id, total_amount AS amount,
                          account, status, requested_at
                   FROM purchase_requests WHERE ticket_id = ? ORDER BY id""", (ticket_id,)
            ).fetchall()]
        linked_invoice = None
        if ticket["invoice_id"] is not None:
            row = connection.execute(
                "SELECT id, vendor_id, amount, due_date, status, description FROM invoices WHERE id = ?",
                (ticket["invoice_id"],),
            ).fetchone()
            linked_invoice = dict(row) if row else None
        linked_lease = None
        if ticket["lease_id"] is not None:
            row = connection.execute(
                "SELECT id, landlord, monthly_rent, next_due FROM leases WHERE id = ?",
                (ticket["lease_id"],),
            ).fetchone()
            linked_lease = dict(row) if row else None
        return {
            "ticket_id": ticket_id, "shop_date": _shop_date(connection).isoformat(),
            "cash_accounts": accounts, "recorded_payments": payments,
            "ticket_payment_requests": pending,
            "ticket_purchase_requests": pending_purchases,
            "linked_invoice": linked_invoice, "linked_lease": linked_lease,
        }


@_registered_tool(read_only=True)
def get_ticket_restock_options(ticket_id: int) -> dict[str, Any]:
    """Show stock and vendor facts without inventing SKU-to-vendor relationships."""
    with _open_database() as connection:
        ticket = _get_ticket(connection, ticket_id)
        if not ticket["sku"] or not ticket["size"]:
            raise ValueError(f"Ticket {ticket_id} does not identify a product SKU and size")
        stock = connection.execute(
            "SELECT sku, name, size, qty, location FROM inventory WHERE sku = ? AND size = ?",
            (ticket["sku"], ticket["size"]),
        ).fetchone()
        vendors = connection.execute(
            "SELECT id, name, specialty, lead_days FROM vendors ORDER BY id"
        ).fetchall()
        options = []
        for vendor in vendors:
            unpaid = connection.execute(
                """SELECT id, amount, due_date, status, description FROM invoices
                   WHERE vendor_id = ? AND lower(status) <> 'paid' ORDER BY due_date, id""",
                (vendor["id"],),
            ).fetchall()
            options.append({
                "vendor": dict(vendor),
                "unpaid_invoices": [dict(row) for row in unpaid],
                "shipment_allowed": len(unpaid) == 0,
            })
        linked_vendor = None
        linked_invoice = None
        if ticket["invoice_id"] is not None:
            row = connection.execute(
                """SELECT id, vendor_id, amount, due_date, status, description
                   FROM invoices WHERE id = ?""", (ticket["invoice_id"],)
            ).fetchone()
            if row:
                linked_invoice = dict(row)
                linked_vendor = row["vendor_id"]
        return {
            "ticket_id": ticket_id, "sku": ticket["sku"], "size": ticket["size"],
            "requested_qty": ticket["qty"],
            "inventory": dict(stock) if stock else None,
            "shortfall_qty": max(ticket["qty"] - stock["qty"], 0) if stock and ticket["qty"] is not None else None,
            "vendor_product_mapping": "not recorded in the database; vendor suitability requires human confirmation",
            "ticket_linked_vendor_id": linked_vendor,
            "ticket_linked_invoice": linked_invoice,
            "vendors": options,
        }


@_registered_tool(read_only=False)
def append_ticket_board_item(
    ticket_id: int, item_type: str, body: str, author: str
) -> dict[str, Any]:
    """Save an internal note or communication draft; never sends it externally."""
    allowed = {"agent_note", "customer_message_draft", "vendor_message_draft"}
    if item_type not in allowed:
        raise ValueError(f"item_type must be one of {sorted(allowed)}")
    body, author = body.strip(), author.strip()
    if not body or len(body) > 4000:
        raise ValueError("body must contain 1 to 4000 characters")
    if not author or len(author) > 80:
        raise ValueError("author must contain 1 to 80 characters")
    with _open_database(writable=True) as connection:
        _ensure_extension_tables(connection)
        connection.execute("BEGIN IMMEDIATE")
        _get_ticket(connection, ticket_id)
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """INSERT INTO ticket_updates (ticket_id, item_type, author, body, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (ticket_id, item_type, author, body, created_at),
        )
        connection.commit()
        return {"update_id": cursor.lastrowid, "ticket_id": ticket_id,
                "item_type": item_type, "author": author, "body": body,
                "created_at": created_at, "sent_externally": False}


@_registered_tool(read_only=True)
def get_ticket_board_items(ticket_id: int) -> list[dict[str, Any]]:
    """Read persisted notes and drafts so recovery can verify saved work without rewriting it."""
    with _open_database() as connection:
        _get_ticket(connection, ticket_id)
        tables = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()}
        if "ticket_updates" not in tables:
            return []
        return [dict(row) for row in connection.execute(
            """SELECT id AS update_id, ticket_id, item_type, author, body, created_at
               FROM ticket_updates WHERE ticket_id = ? ORDER BY id""",
            (ticket_id,),
        ).fetchall()]


@_registered_tool(read_only=False)
def request_payment_approval(
    ticket_id: int, kind: Literal["invoice", "rent"], ref_id: int,
    amount: float, account: str
) -> dict[str, Any]:
    """Queue an exact linked invoice/rent payment for a human; does not pay it."""
    if kind not in {"invoice", "rent"}:
        raise ValueError("kind must be 'invoice' or 'rent'")
    if not math.isfinite(float(amount)) or amount <= 0:
        raise ValueError("Payment amount must be greater than zero")
    if not account.strip():
        raise ValueError("A cash account name is required")

    with _open_database(writable=True) as connection:
        _ensure_extension_tables(connection)
        connection.execute("BEGIN IMMEDIATE")
        ticket = _get_ticket(connection, ticket_id)
        if kind == "invoice":
            if ticket["invoice_id"] != ref_id:
                raise ValueError("Invoice must be the invoice linked to this ticket")
            reference = connection.execute(
                "SELECT id, amount, status FROM invoices WHERE id = ?", (ref_id,)
            ).fetchone()
            if reference is None:
                raise ValueError("Invoice was not found")
            if reference["status"].casefold() == "paid":
                raise ValueError("Invoice is already marked paid")
        else:
            if ticket["lease_id"] != ref_id:
                raise ValueError("Lease must be the lease linked to this ticket")
            reference = connection.execute(
                "SELECT id, monthly_rent AS amount FROM leases WHERE id = ?", (ref_id,)
            ).fetchone()
            if reference is None:
                raise ValueError("Lease was not found")

        amount = round(float(amount), 2)
        if amount != round(float(reference["amount"]), 2):
            raise ValueError("Payment request must match the full recorded invoice or rent amount")
        existing = connection.execute(
            """SELECT id, ticket_id, status FROM payment_requests
               WHERE kind = ? AND ref_id = ? ORDER BY id LIMIT 1""",
            (kind, ref_id),
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"Payment request {existing['id']} for this {kind} obligation "
                f"already exists with status {existing['status']} "
                f"(ticket {existing['ticket_id']}); duplicate requests are not allowed"
            )

        cash = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account.strip(),)
        ).fetchone()
        if cash is None:
            raise ValueError("Cash account was not found")
        reserved = connection.execute(
            """SELECT COALESCE(SUM(amount), 0) FROM payment_requests
               WHERE account = ? AND status = 'pending_approval'""",
            (account.strip(),),
        ).fetchone()[0]
        reserved_purchases = connection.execute(
            """SELECT COALESCE(SUM(total_amount), 0) FROM purchase_requests
               WHERE account = ? AND status = 'pending_approval'""",
            (account.strip(),),
        ).fetchone()[0]
        available_cents = round(float(cash["balance"]) * 100) - round(
            (float(reserved) + float(reserved_purchases)) * 100
        )
        if available_cents < round(float(amount) * 100):
            raise ValueError("Insufficient available funds; payment request was rejected")

        completed = connection.execute(
            """SELECT id FROM payments WHERE kind = ? AND ref_id = ? LIMIT 1""",
            (kind, ref_id),
        ).fetchone()
        if completed is not None:
            raise ValueError(f"This {kind} reference already has a recorded payment")

        requested_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """INSERT INTO payment_requests
               (ticket_id, kind, ref_id, amount, account, status, requested_at)
               VALUES (?, ?, ?, ?, ?, 'pending_approval', ?)""",
            (ticket_id, kind, ref_id, float(amount), account.strip(), requested_at),
        )
        connection.commit()
        return {
            "request_id": cursor.lastrowid, "ticket_id": ticket_id, "kind": kind,
            "ref_id": ref_id, "amount": float(amount), "account": account.strip(),
            "status": "pending_approval", "requested_at": requested_at,
            "requires_human_approval": True, "payment_processed": False,
        }


@_registered_tool(read_only=False)
def request_purchase_approval(
    ticket_id: int, vendor_id: int, qty: int, account: str = "checking"
) -> dict[str, Any]:
    """Queue a shortfall purchase proposal; it neither confirms the supplier nor places an order."""
    if qty <= 0:
        raise ValueError("Purchase quantity must be positive")
    if not account.strip():
        raise ValueError("A cash account name is required")
    with _open_database(writable=True) as connection:
        _ensure_extension_tables(connection)
        connection.execute("BEGIN IMMEDIATE")
        ticket = _get_ticket(connection, ticket_id)
        if not ticket["sku"] or not ticket["size"] or not ticket["qty"]:
            raise ValueError("Ticket does not contain a complete product request")
        stock = connection.execute(
            "SELECT qty FROM inventory WHERE sku = ? AND size = ?",
            (ticket["sku"], ticket["size"]),
        ).fetchone()
        if stock is None:
            raise ValueError("No matching inventory record; purchase quantity cannot be validated")
        shortfall = max(ticket["qty"] - stock["qty"], 0)
        if shortfall == 0 or qty > shortfall:
            raise ValueError(f"Purchase quantity must cover no more than the current shortfall ({shortfall})")
        pricing = connection.execute(
            "SELECT unit_cost FROM pricing WHERE sku = ?", (ticket["sku"],)
        ).fetchone()
        if pricing is None:
            raise ValueError("No recorded unit cost is available for this SKU")
        vendor = connection.execute(
            "SELECT id, name FROM vendors WHERE id = ?", (vendor_id,)
        ).fetchone()
        if vendor is None:
            raise ValueError("Vendor was not found")
        unpaid = connection.execute(
            """SELECT id FROM invoices WHERE vendor_id = ?
               AND lower(status) <> 'paid' LIMIT 1""", (vendor_id,)
        ).fetchone()
        if unpaid is not None:
            raise ValueError("Vendor has an unpaid invoice and cannot ship additional products")
        duplicate = connection.execute(
            """SELECT id FROM purchase_requests WHERE ticket_id = ? AND sku = ?
               AND size = ? AND vendor_id = ? AND status = 'pending_approval' LIMIT 1""",
            (ticket_id, ticket["sku"], ticket["size"], vendor_id),
        ).fetchone()
        if duplicate is not None:
            raise ValueError(f"A purchase proposal is already pending approval (id {duplicate['id']})")
        unit_cost = round(float(pricing["unit_cost"]), 2)
        total_amount = round(unit_cost * qty, 2)
        cash = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account.strip(),)
        ).fetchone()
        if cash is None:
            raise ValueError("Cash account was not found")
        pending_payments = connection.execute(
            """SELECT COALESCE(SUM(amount), 0) FROM payment_requests
               WHERE account = ? AND status = 'pending_approval'""", (account.strip(),)
        ).fetchone()[0]
        pending_purchases = connection.execute(
            """SELECT COALESCE(SUM(total_amount), 0) FROM purchase_requests
               WHERE account = ? AND status = 'pending_approval'""", (account.strip(),)
        ).fetchone()[0]
        available_cents = round(float(cash["balance"]) * 100) - round(
            (float(pending_payments) + float(pending_purchases)) * 100
        )
        if available_cents < round(total_amount * 100):
            raise ValueError("Insufficient available funds; purchase request was rejected")

        requested_at = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute(
            """INSERT INTO purchase_requests
               (ticket_id, sku, size, qty, vendor_id, unit_cost, total_amount,
                account, status, requested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)""",
            (ticket_id, ticket["sku"], ticket["size"], qty, vendor_id,
             unit_cost, total_amount, account.strip(), requested_at),
        )
        connection.commit()
        return {
            "request_id": cursor.lastrowid, "ticket_id": ticket_id,
            "sku": ticket["sku"], "size": ticket["size"], "qty": qty,
            "vendor_id": vendor_id, "vendor_name": vendor["name"],
            "unit_cost": unit_cost, "amount": total_amount,
            "account": account.strip(), "status": "pending_approval",
            "human_vendor_confirmation_required": True,
            "supplier_sku_mapping_confirmed": False,
            "purchase_order_placed": False, "requested_at": requested_at,
        }


@_registered_tool(read_only=False)
def mark_ticket_resolved(ticket_id: int, resolution_note: str) -> dict[str, Any]:
    """Record final disposition only after the latest workflow and ticket evidence pass.

    Out-of-stock customer orders require an explicit, decided human fulfillment
    outcome. That means the blocker was handled, not that goods were delivered.
    This tool never receives or consumes inventory.
    """
    workflow_id = os.environ.get("CAMPUS_CUSTOMS_WORKFLOW_ID")
    if workflow_id and _active_requesting_agent() != "agent":
        raise ValueError(
            "Agents cannot change ticket status; FastAPI invokes this MCP gate after workflow completion"
        )
    resolution_note = resolution_note.strip()
    if not resolution_note or len(resolution_note) > 1000:
        raise ValueError("resolution_note must contain 1 to 1000 characters")
    decisions = _list_decision_records(ticket_id=ticket_id)
    with _open_database(writable=True) as connection:
        _ensure_extension_tables(connection)
        connection.execute("BEGIN IMMEDIATE")
        ticket = _get_ticket(connection, ticket_id)
        current_status = str(ticket["status"]).casefold()
        if current_status in {"resolved", "closed", "complete", "completed"}:
            connection.commit()
            return {
                "ticket_id": ticket_id, "status": "resolved",
                "already_resolved": True, "resolution_note": resolution_note,
                "inventory_consumed": False,
            }
        if current_status not in {"open", "awaiting_decision"}:
            raise ValueError(
                f"Ticket {ticket_id} has unsupported status {ticket['status']!r}"
            )
        outstanding, evidence = _evaluate_final_disposition(connection, ticket, decisions)
        if outstanding:
            connection.commit()
            result = {
                "ticket_id": ticket_id, "status": current_status,
                "resolved": False, "resolution_note": resolution_note,
                "outstanding": outstanding, "evidence": evidence,
                "inventory_consumed": False,
            }
            _append_audit_record({"event": "final_disposition_blocked", **result})
            return result

        updated = connection.execute(
            "UPDATE tickets SET status = 'resolved' WHERE id = ? AND lower(status) IN ('open', 'awaiting_decision')",
            (ticket_id,),
        )
        if updated.rowcount != 1:
            raise ValueError("Ticket status changed during resolution; no resolution was recorded")
        connection.commit()
        result = {
            "ticket_id": ticket_id, "status": "resolved", "resolved": True,
            "already_resolved": False, "resolution_note": resolution_note,
            "evidence": evidence, "inventory_consumed": False,
            "resolved_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    _append_audit_record({"event": "final_disposition", **result})
    return result


if __name__ == "__main__":
    mcp.run()
