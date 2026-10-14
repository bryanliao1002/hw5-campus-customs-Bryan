# Campus Customs Operations Dashboard — Design Notes

## Visual direction

The dashboard is designed as a small-shop operations control room: dark ink surfaces, fine plum borders, warm pink highlights, and a few restrained secondary colors. The black-and-pink palette carries across the project, while subtle radial glows and orbit-like details give it a little character without reducing contrast or making it feel like a toy. Typography uses a compact display face for section hierarchy, a readable system sans for everyday text, and monospaced labels for IDs, tools, and operational metadata. The interface does not depend on remote images or fonts.

## Layout and hierarchy

The narrow top bar identifies Campus Customs, shows API connectivity and the configured backend address, and provides a refresh action. A short welcome heading and shop-date chip lead into four compact summary cards: open tickets, active agent runs, pending human decisions, and the checking balance. This puts the most time-sensitive operational facts above the work area.

The ticket workbench uses a persistent left rail for tickets 101–103 and a larger right-hand detail area for the selected ticket. The detail area layers the actual requester and ticket facts, the ticket's own note, and a prominent “Start agents” action. Below that, the live activity feed shows agent statements, handoffs, and named MCP tool calls in time order. A five-card contribution strip makes each specialist's latest structured summary easy to scan. The finance checkpoint sits below the workbench, keeping approvals visible without competing with the ticket-resolution flow.

## Five-agent visual language

Every role has a two-letter avatar and a consistent color, reused in the crew roster, activity feed, and contribution cards:

| Agent | Color cue | Why it fits the role |
|---|---|---|
| Boss | Bright pink | The team lead and primary decision-maker; this is the dashboard's signature accent. |
| Inventory | Cyan | Suggests stock, product details, and movement without blending into the pink emphasis. |
| Accounting | Amber | Reads as money and careful review, and highlights financial context without implying approval. |
| Facilities | Violet | Separates place, lease, and building work from merchandise and finance. |
| Customer Service | Soft green | Signals calm, customer-facing drafting while remaining clearly distinct from sent communications. |

These colors identify an agent; they do not encode a completion or permission state. Status is always written in text as well as shown by a pill or dot, so color is never the only cue.

## Status, money, and human decisions

Ticket pills distinguish Open, In progress, Needs review, Blocked, and Resolved. A ticket is displayed as Resolved only after the MCP final-disposition gate has updated its stored status; a model report alone cannot resolve it. `needs_human`, `blocked`, and failed outcomes remain explicit. The frontend does not write shop data itself.

Checking cash is formatted as currency and kept in a dedicated high-contrast card. It is read from the checking account endpoint and refreshed after approval, so the visible amount follows the backend's recorded deduction rather than a client-side estimate. Pending payment and purchase proposals appear as separate review cards. Approval opens a confirmation dialog that records the human approver; purchases require an additional explicit confirmation that the vendor can supply the exact SKU and size. The backend and its isolated approval MCP process remain responsible for validation, transaction recording, and preventing negative balances.

Operational human decisions use a separate dashboard queue with the agent's question and recommendation, discrete option buttons, required terms, and a required decision-maker name. This keeps the human choice explicit and auditable while the agents remain unable to record it themselves. For an out-of-stock order, “resolved” means a human selected a fulfillment disposition and its blocker was handled; the UI and audit avoid implying the product was received or delivered.

## Why this should feel useful

The page favors scanability over dense tables: a manager can see what is open, what is moving, who is contributing, and what needs a human decision in one view. The active ticket stays visually anchored while event details scroll independently, preserving context as the team works. Empty states tell the user what to expect, and connection/error states are visible rather than silently showing stale values. Responsive breakpoints collapse the desktop workbench into a mobile ticket selector and stacked panels, while reduced-motion settings quiet the decorative animation.
