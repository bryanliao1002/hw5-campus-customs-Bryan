"""Privileged MCP process for human-approved financial processing only.

This server is launched by FastAPI after the human approval endpoint validates an
explicit approver and confirmation. It is never attached to the PydanticAI agents.
"""

from calendar import monthrange
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from server import (
    _append_audit_record,
    _ensure_extension_tables,
    _get_ticket,
    _open_database,
    _record_human_decision,
    _shop_date,
)


approval_mcp = FastMCP("Campus Customs Human Approval")


@approval_mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True,
        idempotentHint=False, openWorldHint=False,
    )
)
def record_human_decision(
    decision_id: str,
    decided_by: str,
    choice: str,
    terms: str,
) -> dict[str, Any]:
    """Record a pending human decision; this privileged tool is never agent-visible."""
    return _record_human_decision(decision_id, decided_by, choice, terms)


@approval_mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True,
        idempotentHint=False, openWorldHint=False,
    )
)
def reject_financial_request(
    request_type: str,
    request_id: int,
    rejected_by: str,
    reason: str,
) -> dict[str, Any]:
    """Record a human rejection of a pending payment/purchase without moving cash."""
    request_type, rejected_by, reason = (
        request_type.strip().casefold(), rejected_by.strip(), reason.strip()
    )
    if request_type not in {"payment", "purchase"}:
        raise ValueError("request_type must be 'payment' or 'purchase'")
    if not rejected_by or len(rejected_by) > 80:
        raise ValueError("A human reviewer name (1-80 characters) is required")
    if not reason or len(reason) > 1000:
        raise ValueError("A rejection reason (1-1000 characters) is required")
    table = "payment_requests" if request_type == "payment" else "purchase_requests"
    with _open_database(writable=True) as connection:
        _ensure_extension_tables(connection)
        connection.execute("BEGIN IMMEDIATE")
        request = connection.execute(
            f"SELECT * FROM {table} WHERE id = ? AND status = 'pending_approval'",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Pending financial request was not found or was already decided")
        connection.execute(
            f"UPDATE {table} SET status = 'rejected' WHERE id = ? AND status = 'pending_approval'",
            (request_id,),
        )
        connection.commit()
        recorded = {
            "request_type": request_type, "request_id": request_id,
            "ticket_id": request["ticket_id"], "amount": float(
                request["amount"] if request_type == "payment" else request["total_amount"]
            ), "status": "rejected", "decided_by": rejected_by,
            "reason": reason,
        }
    _append_audit_record({"event": "human_financial_decision", **recorded})
    return recorded


def _following_month(value: str) -> str:
    current = date.fromisoformat(value)
    year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    day = min(current.day, monthrange(year, month)[1])
    return date(year, month, day).isoformat()


@approval_mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def approve_and_process(
    request_type: str,
    request_id: int,
    approved_by: str,
    confirm_vendor_can_supply: bool = False,
) -> dict[str, Any]:
    """Revalidate and atomically execute a payment/purchase after API human confirmation."""
    request_type = request_type.strip().casefold()
    approved_by = approved_by.strip()
    if request_type not in {"payment", "purchase"}:
        raise ValueError("request_type must be 'payment' or 'purchase'")
    if not approved_by or len(approved_by) > 80:
        raise ValueError("A human approver name (1-80 characters) is required")
    if request_type == "purchase" and not confirm_vendor_can_supply:
        raise ValueError("A human must confirm this vendor can supply the requested SKU and size")

    with _open_database(writable=True) as connection:
        _ensure_extension_tables(connection)
        connection.execute("BEGIN IMMEDIATE")
        paid_at = _shop_date(connection).isoformat()
        purchase = request_type == "purchase"
        if purchase:
            request = connection.execute(
                """SELECT * FROM purchase_requests WHERE id = ?
                   AND status = 'pending_approval'""", (request_id,)
            ).fetchone()
            if request is None:
                raise ValueError("Pending purchase request was not found")
            ticket = _get_ticket(connection, request["ticket_id"])
            if (ticket["sku"], ticket["size"]) != (request["sku"], request["size"]):
                raise ValueError("Purchase SKU/size no longer matches its ticket")
            stock = connection.execute(
                "SELECT qty FROM inventory WHERE sku = ? AND size = ?",
                (request["sku"], request["size"]),
            ).fetchone()
            if stock is None or ticket["qty"] is None:
                raise ValueError("Purchase stock request can no longer be validated")
            shortfall = max(ticket["qty"] - stock["qty"], 0)
            if request["qty"] > shortfall:
                raise ValueError("Inventory changed; purchase quantity exceeds the current shortfall")
            pricing = connection.execute(
                "SELECT unit_cost FROM pricing WHERE sku = ?", (request["sku"],)
            ).fetchone()
            if pricing is None or round(float(pricing["unit_cost"]), 2) != round(float(request["unit_cost"]), 2):
                raise ValueError("Recorded unit cost changed; a new purchase proposal is required")
            vendor = connection.execute(
                "SELECT id FROM vendors WHERE id = ?", (request["vendor_id"],)
            ).fetchone()
            if vendor is None:
                raise ValueError("Purchase vendor no longer exists")
            unpaid = connection.execute(
                """SELECT id FROM invoices WHERE vendor_id = ?
                   AND lower(status) <> 'paid' LIMIT 1""", (request["vendor_id"],)
            ).fetchone()
            if unpaid is not None:
                raise ValueError("Vendor has an unpaid invoice and is not eligible to ship")
            kind, ref_id = "purchase", request_id
            amount, account = float(request["total_amount"]), request["account"]
        else:
            request = connection.execute(
                """SELECT * FROM payment_requests WHERE id = ?
                   AND status = 'pending_approval'""", (request_id,)
            ).fetchone()
            if request is None:
                raise ValueError("Pending payment request was not found")
            ticket = _get_ticket(connection, request["ticket_id"])
            kind, ref_id = request["kind"], request["ref_id"]
            amount, account = float(request["amount"]), request["account"]
            if kind == "invoice":
                if ticket["invoice_id"] != ref_id:
                    raise ValueError("Payment invoice is no longer linked to its ticket")
                reference = connection.execute(
                    "SELECT amount, status FROM invoices WHERE id = ?", (ref_id,)
                ).fetchone()
                if reference is None or reference["status"].casefold() == "paid":
                    raise ValueError("Invoice is missing or already paid")
                if round(float(reference["amount"]), 2) != round(amount, 2):
                    raise ValueError("Invoice amount changed; payment request is stale")
            elif kind == "rent":
                if ticket["lease_id"] != ref_id:
                    raise ValueError("Rent lease is no longer linked to its ticket")
                reference = connection.execute(
                    "SELECT monthly_rent FROM leases WHERE id = ?", (ref_id,)
                ).fetchone()
                if reference is None or round(float(reference["monthly_rent"]), 2) != round(amount, 2):
                    raise ValueError("Lease rent changed; payment request is stale")
            else:
                raise ValueError("Unsupported payment kind")

        duplicate = connection.execute(
            "SELECT id FROM payments WHERE kind = ? AND ref_id = ? LIMIT 1",
            (kind, ref_id),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("This obligation already has a recorded payment")
        cash = connection.execute(
            "SELECT balance FROM cash_accounts WHERE name = ?", (account,)
        ).fetchone()
        if cash is None:
            raise ValueError("The payment cash account no longer exists")
        if purchase:
            other_payment_reservations = connection.execute(
                """SELECT COALESCE(SUM(amount), 0) FROM payment_requests
                   WHERE account = ? AND status = 'pending_approval'""", (account,)
            ).fetchone()[0]
            other_purchase_reservations = connection.execute(
                """SELECT COALESCE(SUM(total_amount), 0) FROM purchase_requests
                   WHERE account = ? AND status = 'pending_approval' AND id <> ?""",
                (account, request_id),
            ).fetchone()[0]
        else:
            other_payment_reservations = connection.execute(
                """SELECT COALESCE(SUM(amount), 0) FROM payment_requests
                   WHERE account = ? AND status = 'pending_approval' AND id <> ?""",
                (account, request_id),
            ).fetchone()[0]
            other_purchase_reservations = connection.execute(
                """SELECT COALESCE(SUM(total_amount), 0) FROM purchase_requests
                   WHERE account = ? AND status = 'pending_approval'""", (account,)
            ).fetchone()[0]
        available_cents = round(float(cash["balance"]) * 100) - round(
            (float(other_payment_reservations) + float(other_purchase_reservations)) * 100
        )
        amount_cents = round(amount * 100)
        if amount_cents <= 0 or available_cents < amount_cents:
            raise ValueError("Insufficient available funds; no payment was processed")
        new_balance = round((round(float(cash["balance"]) * 100) - amount_cents) / 100, 2)

        connection.execute(
            "UPDATE cash_accounts SET balance = ?, date = ? WHERE name = ?",
            (new_balance, paid_at, account),
        )
        cursor = connection.execute(
            """INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (kind, ref_id, amount_cents / 100, account, paid_at, approved_by),
        )
        if purchase:
            connection.execute(
                "UPDATE purchase_requests SET status = 'processed', approved_by = ? WHERE id = ?",
                (approved_by, request_id),
            )
        else:
            connection.execute(
                "UPDATE payment_requests SET status = 'processed', approved_by = ? WHERE id = ?",
                (approved_by, request_id),
            )
            if kind == "invoice":
                connection.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (ref_id,))
            else:
                current_due = connection.execute(
                    "SELECT next_due FROM leases WHERE id = ?", (ref_id,)
                ).fetchone()["next_due"]
                connection.execute(
                    "UPDATE leases SET next_due = ? WHERE id = ?",
                    (_following_month(current_due), ref_id),
                )
        connection.commit()
        result = {
            "payment_id": cursor.lastrowid, "request_type": request_type,
            "request_id": request_id, "ticket_id": int(request["ticket_id"]),
            "kind": kind, "ref_id": ref_id,
            "amount": amount_cents / 100, "account": account,
            "new_balance": new_balance, "paid_at": paid_at,
            "approved_by": approved_by, "status": "processed",
            "vendor_supply_human_confirmed": confirm_vendor_can_supply if purchase else None,
            "external_order_or_message_sent": False,
        }
    _append_audit_record({
        "event": "human_approval_processed", "result": result,
    })
    return result


if __name__ == "__main__":
    approval_mcp.run()
