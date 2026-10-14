# Campus Customs Database Harness

This reference describes the original starting database, `data/campus_customs.db`. The working copy, `data/campus_customs_new.db`, was present and matched the original when inspected for Problem 2. Table names and field definitions below come from the SQLite schema.

## Tables and fields

### `desk`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `date_today` | TEXT | NOT NULL | Shop's business date. Agents should use it when deciding whether a bill, rent payment, or other dated item is overdue. |
| `notes` | TEXT | — | General shop notes that can provide operating context to agents. |

### `tickets`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY | Ticket identifier used to track work and relate tickets to lease or invoice records. |
| `type` | TEXT | NOT NULL | Describes the work category for routing by the Boss agent. |
| `requester` | TEXT | NOT NULL | Customer, vendor, landlord, or other requester associated with the ticket. |
| `subject` | TEXT | NOT NULL | Short description for the board and agents. |
| `sku` | TEXT | Nullable | Product identifier; agents can use it to look up inventory and pricing. |
| `size` | TEXT | Nullable | Product size used with `sku` to locate a specific inventory row. |
| `qty` | INTEGER | Nullable | Requested product quantity for order or stock calculations. |
| `lease_id` | INTEGER | Nullable; FOREIGN KEY to `leases.id` | Links a ticket to a lease, such as a rent notice. |
| `invoice_id` | INTEGER | Nullable; FOREIGN KEY to `invoices.id` | Links a ticket to a vendor invoice. |
| `status` | TEXT | NOT NULL | Indicates whether the ticket is open or otherwise being handled/completed. |
| `notes` | TEXT | Nullable | Additional request details and constraints for agents. |
| `created_at` | TEXT | NOT NULL | Ticket creation timestamp for the board's history. |

### `inventory`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `sku` | TEXT | Part of composite PRIMARY KEY (`sku`, `size`) | Identifies the product; used to check stock against a ticket or pricing row. |
| `name` | TEXT | NOT NULL | Human-readable product name for agents and the dashboard. |
| `size` | TEXT | Part of composite PRIMARY KEY (`sku`, `size`) | Distinguishes stock for each size or size variant. |
| `qty` | INTEGER | NOT NULL | Available quantity; Inventory uses it to identify shortages and out-of-stock items. |
| `location` | TEXT | NOT NULL | Store location where agents or staff can find the item. |

### `pricing`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `sku` | TEXT | PRIMARY KEY | Connects a product to its cost and list price. |
| `unit_cost` | REAL | NOT NULL | Per-unit cost used by Accounting to estimate purchase cost and margins. |
| `list_price` | REAL | NOT NULL | Standard selling price used to review discounts and profit margins. |

### `vendors`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY | Vendor identifier; invoices refer to it through `invoices.vendor_id`. |
| `name` | TEXT | NOT NULL | Vendor name for Accounting, Inventory, and ticket context. |
| `specialty` | TEXT | NOT NULL | Describes the goods or service the vendor supplies, helping identify potential suppliers. |
| `lead_days` | INTEGER | NOT NULL | Vendor delivery lead time in days, used when estimating replenishment timing. |

### `leases`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY | Lease identifier referenced by `tickets.lease_id`. |
| `space_name` | TEXT | NOT NULL | Identifies the rented shop space. |
| `landlord` | TEXT | NOT NULL | Landlord name for Facilities and Accounting follow-up. |
| `monthly_rent` | REAL | NOT NULL | Rent amount used to review or prepare rent payments. |
| `next_due` | TEXT | NOT NULL | Next rent due date; compare with `desk.date_today` for overdue checks. |
| `notes` | TEXT | Nullable | Extra lease or facilities context. |

### `invoices`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY | Invoice identifier referenced by `tickets.invoice_id` and potentially `payments.ref_id`. |
| `vendor_id` | INTEGER | NOT NULL; FOREIGN KEY to `vendors.id` | Identifies the supplier owed money. |
| `amount` | REAL | NOT NULL | Amount owed, used by Accounting to check funds and prepare a payment for human approval. |
| `due_date` | TEXT | NOT NULL | Invoice deadline; compare with `desk.date_today` to determine overdue status. |
| `status` | TEXT | NOT NULL | Indicates whether the invoice is open, paid, or otherwise resolved; open unpaid invoices affect whether more goods can be shipped. |
| `description` | TEXT | Nullable | Explains what the invoice covers, including related products or services. |

### `cash_accounts`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `name` | TEXT | PRIMARY KEY | Account identifier used when choosing the source of an outgoing payment. |
| `balance` | REAL | NOT NULL | Available funds. Accounting must reject a payment that would make this balance negative. |
| `date` | TEXT | NOT NULL | Date associated with the account balance. |

### `payments`

| Field | Type | Key / constraint | Use |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY | Identifier for a recorded payment. |
| `kind` | TEXT | NOT NULL | Payment category, such as an invoice or rent payment. |
| `ref_id` | INTEGER | Nullable | Reference to the related invoice, lease, or other record depending on `kind`; the schema does not declare a foreign key for it. |
| `amount` | REAL | NOT NULL | Amount paid, used to record cash leaving the business. |
| `account` | TEXT | NOT NULL | Cash account charged by the payment. |
| `paid_at` | TEXT | NOT NULL | Date/time when the payment was processed. |
| `approved_by` | TEXT | NOT NULL | Records the human approver for the payment. Payments must receive human approval before processing. |

## Open tickets and their related data

The shop date in `desk.date_today` is **2026-08-31**. It is the reference date for due-date checks.

### Ticket 101 — Bulldog tee customer order

- `tickets.sku` and `tickets.size` are `CC-TEE-WHITE` and `S`. The matching `inventory` row has quantity **0**, so the requested tee is out of stock.
- The same SKU has a `pricing` row with unit cost **$8.00** and list price **$28.00**.
- `tickets.invoice_id = 501` links directly to `invoices.id = 501`. At the original start, invoice 501 was **$840.00**, due **2026-08-28**, and open. Its vendor is `vendors.id = 1` (Bulldog Print Co.), specialty apparel reprint, lead time **5 days**; the invoice description says “Rush reprint CC-TEE-WHITE S,” so the database identifies Bulldog as the historical source/candidate for this exact item. That does not establish current availability or shipment. In the intermediate state after human approval, invoice 501 was paid once (payment ID 1) and checking was $2,560.00; after Ticket 102's rent payment, the final checking balance is **$160.00**. Ticket 101 is now operationally resolved after a human chose to defer fulfillment pending confirmed exact-item restock. Size-S stock remains zero, so this is not a delivery.
- Whenever the vendor has an open unpaid invoice, the system must block an additional shipment from that vendor until payment; a historical vendor/product link does not establish supply. Any payment requires human approval and sufficient available cash.

### Ticket 102 — Rent due

- `tickets.lease_id = 1` links directly to `leases.id = 1`, the Chapel Street shop lease with Elm City Properties.
- At the Problem 9 run, the lease records monthly rent of **$2,400.00** and original `next_due` **2026-09-02**. Relative to the shop date of 2026-08-31, rent was due in two days, consistent with the ticket note; it was not overdue. After the human-approved payment, the current working row advances `next_due` to **2026-10-02** and the ticket is resolved.

### Ticket 103 — Bulk hoodie discount request

- The ticket requests **20** `CC-HOOD-NAVY` hoodies in size **M**. The matching inventory row has **8**, leaving a shortfall of **12** if all 20 are to be supplied from stock.
- The SKU's `pricing` row gives a unit cost of **$22.00** and list price of **$58.00**. Accounting can use these values to review a proposed discount and its margin.
- No target unit price or discount amount is supplied. Without one, agents must not invent an offer; the supported path is a fact-matched Accounting note with `offer_unit_price=null` and a Customer Service draft asking the customer for a target. A human discount choice, if already recorded, is reused rather than requested again.
- For this price-override ticket, Boss routes the fact check to Accounting (whose MCP product context contains both stock and pricing), then passes those facts to Customer Service for a draft. It does not delegate Inventory just to repeat the same lookup, and it must never request a `fulfillment` decision for this non-order ticket.
- There is no explicit SKU-to-vendor relationship in the schema. Bulldog Print Co.'s apparel reprint specialty and invoice description do not prove it can supply the exact product/size. Its `lead_days` value is 5, but suitability requires human confirmation. The system must check outstanding invoices before any shipment.

## Schema relationship notes

- Declared foreign keys are `tickets.lease_id → leases.id`, `tickets.invoice_id → invoices.id`, and `invoices.vendor_id → vendors.id`.
- Product relationships use `sku` (and, for stock, `size`) as matching values; `tickets`, `inventory`, and `pricing` do not declare foreign keys between those records.
- `payments.ref_id` is a polymorphic reference by convention and has no declared foreign key. The application must interpret it using `payments.kind` and validate the target itself.
- No payment rows were present in the starting database. The starting checking balance was **$3,400.00**.

## Problem 5 — Agents, MCP tools, and safety

### PydanticAI agents

Every role has its own detailed system prompt in `backend/prompts/`; `backend/agents.py` creates all five as PydanticAI agents with typed `AgentReport` outputs from `backend/models.py`. They share one model object configured only as `gpt-6-luna` through `https://api.portkey.ai/v1`. Authentication is read from `PORTKEY_API_KEY` in the process environment or root `.env`. There is no other model or fallback configuration.

Every agent receives the same MCP stdio toolset and the same `delegate_to_peer` tool. Each agent may delegate to any of the other four roles; self-delegation and calls that would repeat a role already on the current delegation chain are rejected. Boss triages the queue, selects specialists, reviews their results, and reports a final disposition.

| Agent | Prompt file | Responsibility and authorized decisions |
|---|---|---|
| Boss | `backend/prompts/boss.md` | Reviews the MCP ticket queue, routes work, delegates as needed, reconciles findings, and makes the final recommendation. Cannot approve/execute payments or send communications. |
| Inventory | `backend/prompts/inventory.md` | Checks SKU/size stock, shortfalls, recorded vendor specialties and lead times, outstanding invoice shipment blocks, and explicitly unconfirmed SKU/vendor associations. Cannot place orders or promise stock/delivery. |
| Accounting | `backend/prompts/accounting.md` | Reviews source records for cash, invoices, rent, costs, list prices, margins, and exact payment proposals. Can queue a request for human approval only; cannot approve or process it. |
| Facilities | `backend/prompts/facilities.md` | Reviews leases, rent notices, physical-space matters, and date timing using the stored shop date; escalates payment review to Accounting. Cannot contact a landlord or authorize payment. |
| Customer Service | `backend/prompts/customer_service.md` | Prepares factual customer message drafts and saves them on the ticket board. Cannot send messages, promise unverified fulfillment, or quote an unapproved discount. |

### Current MCP tool catalogue

The server uses `data/campus_customs_new.db` exclusively. All reads below are served by MCP; all writes are validated MCP actions. The original database remains a reset source.

| MCP tool | Tables accessed | Tickets supported and specific use |
|---|---|---|
| `get_ticket_product_context(ticket_id)` (read) | `tickets`, `inventory`, `pricing` | 101: confirms the white tee size S has zero stock and supplies cost/list price. 103: compares a request for 20 navy size-M hoodies with 8 on hand (shortfall 12), and gives the values for margin review. |
| `get_ticket_supplier_context(ticket_id)` (read) | `tickets`, `invoices`, `vendors`, `desk` | 101: follows invoice 501 to Bulldog Print Co., returns its 5-day vendor-recorded lead time, finds the unpaid invoice, and checks overdue status using the stored shop date. |
| `get_ticket_rent_context(ticket_id)` (read) | `tickets`, `leases`, `desk` | 102: follows lease 1 to the Chapel Street lease, Elm City Properties, $2,400 rent, and September 2 due date; compares it to August 31 shop date. |
| `list_open_tickets()` (read) | `tickets` | Boss discovers all currently open tickets and their recorded request details before routing. |
| `list_tickets()` (read) | `tickets` | The dashboard reads every ticket and its status to show open versus resolved work. |
| `get_cash_balance(account_name="checking")` (read) | `cash_accounts` | Returns the checking balance and recorded balance date for the dashboard. |
| `list_pending_approval_requests()` (read) | optionally `payment_requests`, `purchase_requests`, `tickets`, `vendors`, `sqlite_master` | Shows pending payment and purchase proposals in a dashboard-friendly form; it does not approve them. |
| `request_human_decision(ticket_id, kind, question, recommendation, options)` (write) | `tickets`; external `output/human_decisions.json` | Queues one `fulfillment`, `discount`, or `other` choice per ticket/kind in a file-locked JSON store. Retries reuse the existing pending or decided record (including its human choice/terms) rather than duplicating or replacing it; never changes ticket status. |
| `list_decisions(ticket_id=None, status=None)` (read) | External `output/human_decisions.json` | Returns decision requests and human outcomes; supports ticket/status filters. |
| `get_ticket_financial_context(ticket_id)` (read) | `tickets`, `cash_accounts`, `payments`, `invoices`, `leases`, optionally `payment_requests`, `purchase_requests`, `desk`, `sqlite_master` (detects optional queue tables) | Accounting reviews current cash, recorded payments, linked obligations, shop date, and pending proposals for that ticket. |
| `get_ticket_restock_options(ticket_id)` (read) | `tickets`, `inventory`, `vendors`, `invoices` | 101/103: reports exact product shortage, vendor specialties/lead days, unpaid invoice shipment blocks, and (for 101) the linked invoice description naming “Rush reprint CC-TEE-WHITE S” and vendor ID 1. This makes Bulldog Print Co. an evidence-backed historical candidate, not a guarantee of current supply; human confirmation is still required. |
| `append_ticket_board_item(ticket_id, item_type, body, author)` (write) | `tickets`, `ticket_updates` | Adds an internal note or clearly labeled customer/vendor message draft to the ticket. It has no send/contact behavior. |
| `get_ticket_board_items(ticket_id)` (read) | `tickets`, `ticket_updates` (if initialized) | Returns persisted notes and drafts by update ID so recovery can verify prior work through MCP without re-appending it. Ticket 103 recovery matches the failed run's Accounting note and Customer Service draft to these stored rows. |
| `request_payment_approval(ticket_id, kind, ref_id, amount, account)` (write) | `tickets`, `invoices` or `leases`, `cash_accounts`, `payments`, `payment_requests`, `purchase_requests` | 101/102: validates an exact linked invoice or rent amount, rejects insufficient funds and duplicate requests/payments, reserves cash across both proposal types, and queues `pending_approval`. It never approves or processes a payment. |
| `request_purchase_approval(ticket_id, vendor_id, qty, account="checking")` (write) | `tickets`, `inventory`, `pricing`, `vendors`, `invoices`, `cash_accounts`, `payment_requests`, `purchase_requests` | Prepares a proposal for a ticket's actual stock shortfall at recorded cost. It blocks vendors with unpaid invoices, reserves funds, flags missing vendor/SKU confirmation, and does not place an order. |
| `mark_ticket_resolved(ticket_id, resolution_note)` (write; invoked by FastAPI after workflow completion) | `tickets`, `inventory`, `invoices`, `leases`, `payments`, `payment_requests`, `purchase_requests`, `ticket_updates`, `pricing`; external workflow audit and decision JSON | Uses one documented final-disposition function. It requires the latest ticket workflow to be completed and every payment/purchase proposal to be human-approved/processed or rejected. A customer order also needs any linked invoice paid; if exact stock has a shortfall, it needs a decided human fulfillment choice with terms. It never consumes or increases stock. A resolved out-of-stock order means the blocker was handled and a human chose its disposition; it does **not** mean delivery. Rent still needs its linked rent payment; price override still needs its fact-matched note and customer draft. Missing conditions leave the ticket open and are returned and audited exactly. The MCP server rejects status-change attempts during agent calls. |

The standard agent MCP server does not expose a payment execution/approval tool.

### Human-approval MCP tool (separate process; not agent-accessible)

| MCP tool | Tables accessed | Purpose |
|---|---|---|
| `approve_and_process(request_type, request_id, approved_by, confirm_vendor_can_supply=false)` | `payment_requests` or `purchase_requests`, `tickets`, `invoices` or `leases`, `vendors`, `inventory`, `pricing`, `cash_accounts`, `payments`, `desk` | FastAPI invokes this privileged tool only after a local human explicitly approves. It revalidates obligation and cash, prevents duplicate payment/negative balances, then transactionally records payment, cash deduction, and request status. Purchase approval additionally requires human vendor/SKU confirmation, but records no receipt. Not agent-accessible. |
| `reject_financial_request(request_type, request_id, rejected_by, reason)` | `payment_requests` or `purchase_requests` | Records a human rejection of one pending proposal; releases its reservation by changing status, moves no cash, and is unavailable to agents. |
| `record_human_decision(decision_id, decided_by, choice, terms)` | External `output/human_decisions.json` | Human-only; requires a known pending ID, a listed option, approver identity, and non-empty terms. The store timestamps with current UTC, prevents re-decision, and appends a `human_decision` audit event. It does not change ticket status. |

`ticket_updates`, `payment_requests`, and `purchase_requests` are application-owned tables initialized lazily in the working database by MCP writes. Operational decisions are stored outside SQLite in a sidecar-locked JSON file; no new shop-database table or column is added. Existing source-table schemas above describe the original database; no original records or schema are changed by this design.

For regression testing only, `CAMPUS_CUSTOMS_DB_PATH` and `CAMPUS_CUSTOMS_AUDIT_PATH` may point both processes at disposable copies. Production defaults remain the HW5 working database and `output/audit_trail.json`.

### Audit trail and safeguards

- Each model-agent invocation records its role, parent, depth, ticket/task, structured result, status, and usage. Delegations record source and target. The MCP server records each tool name, inputs, actual output or error. Records append to `output/audit_trail.json` as a valid JSON array; a shared file lock prevents concurrent agent/server appends from discarding earlier entries.
- `resolve_open_tickets()` resets the working database from the original before a full run by default, and refuses to reset if SQLite sidecars indicate an active database user. It does not run automatically on import.
- All agent-facing facts/tools use one stdio MCP server. Backend agent code does not query SQLite. Database initialization of the three app-owned extension tables is limited to MCP writes.
- No external email, phone, vendor contact, shipment, or order action exists. Customer/vendor communication is saved only as a draft in `ticket_updates`.
- For an out-of-stock order, resolved means the blocker was handled and a human explicitly chose the fulfillment outcome; it does not mean the item was delivered. The current team cannot receive goods. A future human-only `receive_purchase_order` should record actual quantity against an approved PO and update inventory transactionally, owned by Inventory with Accounting reconciling; it is not implemented now.
- Agent tools only queue payment/purchase proposals. FastAPI invokes a separate human-approval MCP process only after explicit human confirmation; that process rechecks current funds, pending reservations, the obligation and vendor eligibility in a write transaction before recording the approver, inserting `payments`, reducing cash, and updating the request and linked invoice/lease. A human rejection records a reviewer and reason without reducing cash. After either decision, FastAPI rechecks final disposition; purchases require explicit confirmation that the vendor can supply the exact item but approval does not record receipt. Money only leaves the business; balances must never become negative.
- Human approval does not automatically launch an agent run. `GET /api/tickets/{ticket_id}/continuation` verifies a latest `needs_human` run, its own successful payment proposal, the processed request, and the matching recorded payment; the dashboard then offers an explicit Continue action. `POST /api/tickets/{ticket_id}/continue` repeats those checks and rechecks for an active queued/running job immediately before queueing, preventing duplicate concurrent continuations. Ordinary starts also reject queued or running jobs. Continuation tells Boss to re-read state and never request/process the same obligation again. The payment tool rejects already-paid or previously requested obligations, and the approval transaction rejects duplicate ledger entries.
- An agent-reported `complete` is not sufficient on its own: after the workflow completes, FastAPI invokes the documented MCP final-disposition check and verifies the stored ticket status before exposing a completed run. In the intermediate state, Ticket 101 remained open after invoice 501 was paid while size-S stock was short and no human fulfillment outcome existed. Once that choice was recorded and Boss synthesis completed, the gate resolved the ticket without creating stock or asserting delivery.
- For Ticket 103, Boss checks `list_decisions` before asking for human input; a saved discount choice controls the draft, and `request_human_decision` reuses the existing pending/decided record by ticket and kind on retry. The price override is resolved only after the exact Accounting note and Customer Service draft exist; this does not promise that the 12-unit shortfall is restocked or that a discount has been granted.
- Portkey key material is loaded from `PORTKEY_API_KEY` and is not stored in code or audit records. The model name and Portkey URL are fixed in one backend configuration; no fallback model is configured.
- Usage is bounded to 9 agent runs per workflow, 4 delegation levels, 8 model requests and 12 tool calls per agent run, at most 14,000 tokens per agent run, and 24,000 tokens across the workflow. Boss's Ticket 103 synthesis reached 13,531 tokens against the former 12,000 per-agent cap while the aggregate stayed below 24,000, so the per-agent cap was raised modestly; Ticket 103 also now routes directly through Accounting and Customer Service to avoid a redundant Inventory delegation and repeated product lookup. The shared cap remains unchanged. Failed agent calls add observed usage to the workflow budget and record requests, tool calls, and tokens in the failure event. The required model remains `gpt-6-luna` through Portkey, with `reasoning_effort="none"` for tool calls on the supported Chat Completions route; no fallback model is configured. Calls are sequential, self-delegation and cycles are blocked, and prompts tell agents to avoid repeated research.
- Nested token accounting charges only each invocation's newly observed usage, accounts a paused parent's usage before delegation, and shrinks its remaining allowance after the child returns. Each normal agent call also has a 2,400-token cumulative output cap; a 1,200-token reserve is retained before a parent can resume after delegation. When that headroom is exhausted, the parent cannot issue another model request. This keeps the shared 24,000-token workflow budget consistent instead of granting a parent a stale pre-delegation allowance.
- Tickets 101 and 103 have narrowly scoped Boss-synthesis recovery for a latest Boss token-limit failure only. Ticket 101 requires exactly one persisted successful result for Inventory, Accounting, and Customer Service; fresh MCP evidence must confirm its $840 invoice payment was recorded once, checking remains $2,560, its human fulfillment choice is decided, stock is still short, and the ticket is open. Ticket 103 requires exactly one saved Accounting and Customer Service result plus their successful MCP board-write records; current MCP reads must confirm the single decided “Ask customer for a target unit price” choice, current 8/20 stock and $22/$58 pricing, the exact structured Accounting note and unsent draft each exist once, no unresolved decision/financial request, and the ticket is open. Recovery supplies compact evidence to Boss alone, with no tools or delegation, so it cannot repeat payment, decision, or board writes. Ticket 103's Boss-only pass is capped at 3,000 tokens, 700 output tokens, and two requests. The original failure remains in the append-only audit; a distinct recovery workflow must complete, and the ordinary MCP final-disposition gate must still pass before status changes. Duplicate, corrupt, or stale evidence is rejected before model invocation.
- For Ticket 103, Boss avoids the redundant Inventory delegation and lookup, Accounting does not re-list the already identified open ticket, and Boss does not call `mark_ticket_resolved` prematurely. FastAPI invokes the MCP status gate only after workflow completion; the MCP server rejects status changes during an active agent call. Boss returns a concise synthesis and cannot directly resolve a ticket.
- Failed model/workflow calls are surfaced as failed in the dashboard and audit trail; they are not automatically retried because a prior turn may already have created a draft or pending proposal. Review that ticket's audit/activity and current MCP state before manually starting a new run. Financial requests remain duplicate-protected, human decisions remain human-only, and approvals are never retried or made by the agents.
- Ticket summaries should include only information needed to resolve the task. Do not place API credentials or unnecessary customer details in notes, drafts, or audit records.

### Problem 7 — FastAPI dashboard routes

Run from `backend/` with `uvicorn main:app --reload --port 8000`. The service uses Uvicorn's local default host; approval and reset routes reject non-loopback clients. Ticket, balance, and approval-queue reads use the standard MCP server. Payment execution uses the separate privileged approval MCP process, which is never attached to PydanticAI agents.

| Method and URL | Purpose |
|---|---|
| `GET /api/tickets` | Returns every ticket, its stored status, and explicit `is_open`/`is_resolved` flags. |
| `POST /api/tickets/{ticket_id}/run` | Starts agents asynchronously for one open ticket and returns a run ID; it rejects a duplicate queued/running workflow and refuses a full-team rerun while Ticket 101 or 103 has eligible or in-progress Boss-synthesis recovery evidence. It does not reset the database. |
| `GET /api/tickets/{ticket_id}/continuation` | Verifies whether a latest `needs_human` run has a matching human-processed payment; returns eligibility and payment evidence without starting agents. |
| `POST /api/tickets/{ticket_id}/continue` | Explicit dashboard-triggered continuation, allowed only when the latest run's exact payment proposal is processed and matches a recorded payment; rechecks active-run state before queueing and never runs automatically on approval. |
| `GET /api/tickets/{ticket_id}/recovery-candidate` | Read-only audit check for the narrow Ticket 101/103 Boss-synthesis recovery; returns the source workflow ID and saved specialist roles without calling an AI model, and reports an audit-persisted recovery already in progress. |
| `POST /api/tickets/{ticket_id}/recover-synthesis` | Requires the exact failed workflow ID, local dashboard access, complete persisted specialist evidence, no active run, and fresh MCP prerequisite checks. Queues one Boss-only synthesis run; it never reruns specialists, repeats payment/decision/board writes, or directly changes ticket status. Ticket 103 requires its recorded discount decision, one matching persisted Accounting note and unsent Customer Service draft, current product facts, and no unresolved financial approvals. The normal final-disposition gate remains mandatory. |
| `GET /api/runs/{run_id}` | Returns the status and result for a dashboard-started run. Run state is in memory and is lost on reload; audit history remains. |
| `GET /api/activity?limit=80&ticket_id={id}` | Returns recent agent statements/results, delegations, MCP tool names/results, and errors from the append-only audit file. |
| `GET /api/approvals` | Lists pending payment and purchase requests for human review. |
| `POST /api/approvals/{request_type}/{request_id}/approve` (`request_type` is `payment` or `purchase`) | Requires approver identity and explicit confirmation; purchases additionally require confirmation of vendor capability. The private MCP action revalidates funds and obligations, then transactionally records payment, updates cash and request status, and marks an invoice paid or advances the rent due date. FastAPI then rechecks final disposition without rerunning agents. |
| `POST /api/approvals/{request_type}/{request_id}/reject` | Requires a local human's identity and reason; records a rejection without a cash deduction, then rechecks final disposition. |
| `GET /api/decisions?ticket_id={id}&status=pending` | Reads the separate JSON-backed operational decision queue through MCP. |
| `POST /api/decisions/{decision_id}` | Records a human-only choice, approver, and terms through the isolated approval MCP, audits all fields, then separately rechecks final disposition; the decision tool itself never changes ticket status. |
| `GET /api/cash/checking` | Returns the checking balance through MCP. |
| `POST /api/database/reset` | Refuses while an agent run is active; otherwise restores the working database from the unchanged original without clearing audit history. |

### Problem 8 — React operations dashboard

The TypeScript React/Vite dashboard lives in `frontend/`. Run it with `npm run dev` from that directory. The assignment configuration binds Vite to **5173** and FastAPI to **8000**; `VITE_API_BASE_URL` controls the API endpoint and defaults to `http://localhost:8000`. FastAPI permits the Vite origin `http://localhost:5173`.

The dashboard presents all three ticket records and their database-backed statuses, allows an explicit ticket run/continuation or eligible Boss-only synthesis recovery, and displays the checking balance, approval queue, decision cards, agent activity, and each agent's contribution summary while live run data is available. Ticket 101's decided defer-until-restock outcome is shown with the secondary **Awaiting Restock** badge. Human approval and decision buttons remain explicit; the UI does not automatically approve requests. The screenshots captured after completion are in `output/resolved_board.html` with the associated `output/dashboard-ticket-*.png` images.

### Application-owned working tables and decision sidecar

The original shop schema above remains the source schema. MCP initialized these additional operational tables in the working database; they are not present in the original reset source.

| Table | Fields | Agent / workflow use |
|---|---|---|
| `ticket_updates` | `id` INTEGER PRIMARY KEY; `ticket_id` INTEGER NOT NULL; `item_type` TEXT NOT NULL; `author` TEXT NOT NULL; `body` TEXT NOT NULL; `created_at` TEXT NOT NULL | Stores internal agent notes and customer/vendor message drafts on the ticket board. Draft records are not sent externally. Ticket 101 has Customer Service draft 1; Ticket 103 has Accounting note 2 and Customer Service draft 3. |
| `payment_requests` | `id` INTEGER PRIMARY KEY; `ticket_id` INTEGER NOT NULL; `kind` TEXT NOT NULL; `ref_id` INTEGER NOT NULL; `amount` REAL NOT NULL; `account` TEXT NOT NULL; `status` TEXT NOT NULL; `requested_at` TEXT NOT NULL; `approved_by` TEXT nullable | Holds exact agent proposals separately from the payment ledger. Agents may create proposals; only the human approval process may change one to processed and create a payment. Final rows 1 and 2 are processed; neither is a duplicate. |
| `purchase_requests` | `id` INTEGER PRIMARY KEY; `ticket_id` INTEGER NOT NULL; `sku` TEXT NOT NULL; `size` TEXT NOT NULL; `qty` INTEGER NOT NULL; `vendor_id` INTEGER NOT NULL; `unit_cost` REAL NOT NULL; `total_amount` REAL NOT NULL; `account` TEXT NOT NULL; `status` TEXT NOT NULL; `requested_at` TEXT NOT NULL; `approved_by` TEXT nullable | Holds proposed replenishment purchases and approval/confirmation state. There are no purchase requests in the final run; no shipment or goods receipt is implied. |

Human operational choices live in the file-locked external `output/human_decisions.json`, not in SQLite. Each decision stores `decision_id`, `ticket_id`, `kind`, `question`, `recommendation`, `options`, `status`, `created_at`, `requested_by`, `workflow_id`, and (when decided) `decided_by`, `choice`, `terms`, and `decided_at`. The final records include Ticket 101's explicit fulfillment disposition and Ticket 103's discount-process choice.

### Problem 9 — final database verification and cash reconciliation

The final checks below were made against the current working DB, the decision sidecar, and the append-only audit trail. No reset or agent run was performed while preparing these deliverables. SQLite `integrity_check` returned `ok`.

| Ticket | Final DB status | Recorded outcome | Human finance action | Human operational decision | Cash change |
|---|---|---|---|---|---:|
| 101 | `resolved` | Invoice blocker paid; human chose defer-until-confirmed-restock for one out-of-stock size-S tee. Stock remains 0; no receipt/delivery. Dashboard badge: Awaiting Restock. | Invoice 501 payment $840, ledger ID 1, approved by Tauhid Zaman; invoice status `paid`. | Fulfillment decision `e5e05637-63a3-45bc-ae35-7f7c60e5ffe1`: defer pending human-confirmed restock, no promised date. | −$840.00 |
| 102 | `resolved` | $2,400 rent for lease 1 processed; `leases.next_due` is now 2026-10-02 (the run verified original due date 2026-09-02 against business date 2026-08-31). | Rent payment, ledger ID 2, approved by Bryan Liao. | None recorded or needed. | −$2,400.00 |
| 103 | `resolved` | Discount request handled by asking for a target unit price and preserving an unsent draft. It is not evidence of a granted discount or fulfillment of 20 hoodies; 8 were in stock and 12 short. | No payment or purchase request. | Discount decision `2f6d370b-3e48-4144-a23c-22ca55694089`: “Ask customer for a target unit price,” recorded by Bryan Liao. | $0.00 |

Starting checking was **$3,400.00**, verified in the original data immediately after the run reset. The two and only two ledger rows are invoice 501 for $840 and rent for $2,400. There are no purchase requests. The reconciled final amount is `$3,400.00 − $840.00 − $2,400.00 + $0.00 = $160.00`, exactly the current `cash_accounts.balance` for checking; it is nonnegative. The current shop balance date remains 2026-08-31.

### Problem 9 — actual agent participation and recovery record

- **Ticket 101:** Inventory established the exact stock shortfall and reviewed supplier facts without treating the historical invoice/product link as proof of current supply; Accounting prepared the invoice proposal; Customer Service saved an honest, unsent draft. Their completed outputs came from workflow `208233eb-e086-4829-8294-35e4eab442c2`, whose Boss synthesis failed at 14,308 tokens, after an earlier request-limit failure. Once the payment and fulfillment choice existed, Boss-only recovery workflow `ee7a96a6-f7ed-4d42-8f63-6434545a4292` reused those saved reports. The final MCP disposition marked the ticket resolved without consuming stock. The Boss recovery text contains a stale sentence saying the ticket was not resolved, but the later `final_disposition` event and current database both say resolved; the status does not assert delivery.
- **Ticket 102:** Boss delegated to Facilities, which checked the lease and stored shop date. Boss queued the exact rent proposal; the audit shows no Accounting-agent invocation for this ticket. The human approval route processed it and the MCP final-disposition check resolved the ticket.
- **Ticket 103:** The first run (`fa4802ff-beb4-4861-950d-25585821af26`) saved Inventory/Accounting analysis and failed in Boss synthesis at 13,531 tokens against the former 12,000 limit. A wrong-kind `fulfillment` decision request was rejected; a valid `discount` request was saved and later decided. In the second run (`206f4623-e1e3-4c70-a109-c080fbdc6e55`), Accounting saved the exact pricing note and Customer Service saved the target-price draft. Its attempted `mark_ticket_resolved` returned `resolved=false` because that workflow had not completed; Boss then failed at 11,461 tokens against an effective 10,435 limit. Boss-only recovery (`fd0d24a9-b9d3-440e-951f-d43bf9835e25`) reused verified saved evidence, completed synthesis, and passed the normal final gate. No specialist was rerun, and no payment, purchase, draft, or decision was duplicated.

All those terminal failures, their successful specialist records, human actions, recovery preflights, Boss recovery completions, and final-disposition events remain in `output/audit_trail.json`; it was not cleared or rewritten for Problem 9. The dashboard screenshots are genuine captures from the live React app at `http://localhost:5174`. After backend reload, its in-memory run counter is `00` and the short activity view may be quiet even though the complete execution evidence remains in the append-only audit; the dashboard does not persist old run objects in memory across reloads. Full ticket-by-ticket evidence is in `output/resolved_tickets.json` and `output/desk_tickets.html`.
