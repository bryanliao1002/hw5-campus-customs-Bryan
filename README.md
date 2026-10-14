# Campus Customs Multi-Agent Operations (HW5)

Campus Customs is a local operations workflow for three shop tickets. Five PydanticAI agents—Boss, Inventory, Accounting, Facilities, and Customer Service—coordinate through the shared FastMCP server. The React dashboard lets a person review activity and explicitly approve financial requests or record operational decisions.

All application model calls use **`gpt-6-luna` through Portkey** with `PORTKEY_API_KEY`. There is no alternate-model or direct-provider fallback. Customer/vendor messages remain drafts, and agents cannot approve or execute payments.

## Project data and safeguards

- `data/campus_customs.db` is the original, read-only reset source.
- `data/campus_customs_new.db` is the working database used by the MCP server and FastAPI backend. Keep the two files separate.
- The three Problem 9 ticket outcomes and payment ledger are preserved in the working copy and documented in `output/resolved_tickets.json`; the audit history is append-only in `output/audit_trail.json`.
- Human operational decisions are stored outside SQLite in `output/human_decisions.json` when created at runtime. That local/run-specific file is ignored by Git; the completed decisions are also documented in the resolved-ticket and audit deliverables.
- The standalone resolved-board page references `dashboard-ticket-101.png`, `dashboard-ticket-102.png`, and `dashboard-ticket-103.png` beside the HTML file, so keep all three `output/` screenshots when copying or publishing the project.

## Setup

Use Python 3.11 or newer and Node.js 22.12 or newer. From the project root:

```sh
cp .env.example .env
# Edit .env locally and replace the placeholder with your Portkey key.

python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -r requirements.txt

python3 -m venv mcp_server/.venv
mcp_server/.venv/bin/python -m pip install --upgrade pip
mcp_server/.venv/bin/python -m pip install -r mcp_server/requirements.txt

cd frontend
npm ci
```

`.env` and frontend `.env.local` are private local files and must never be committed. The tracked `.env.example` contains only a placeholder. The frontend API endpoint is configurable with `VITE_API_BASE_URL`; its provided example and default are `http://localhost:8000`.

## MCP server

The project-root `.mcp.json` configures `mcp_server/server.py` as the `campus_customs` stdio MCP server. Restart/reload the MCP client after cloning so it reads that configuration. To launch the server from an MCP-aware client manually, use `mcp_server/.venv/bin/python mcp_server/server.py` from the project root. This is a stdio process, not an HTTP service; the FastAPI workflow also starts the same MCP server automatically when agents run. The shared server connects to `data/campus_customs_new.db`, never the original database.

## Run the backend and dashboard

Start FastAPI in one terminal:

```sh
cd backend
../backend/.venv/bin/uvicorn main:app --reload --port 8000
```

Start Vite in another terminal:

```sh
cd frontend
npm run dev
```

The backend is at `http://localhost:8000` and the dashboard is at `http://localhost:5173`. FastAPI allows the Vite origin. From `frontend/`, run `npm run build` to type-check and build the dashboard. The API includes local-only human approval and database-reset routes; all real payments require human approval and insufficient funds are rejected.

## Start a fresh ticket-resolution run

Before resolving all three tickets as a new run, restore the working database from the original **once, before starting Ticket 101**. Do not reset between tickets 101, 102, and 103. Start the backend, then use the dashboard's reset action or the local-only route:

```sh
curl -X POST http://localhost:8000/api/database/reset
```

This route copies `data/campus_customs.db` over `data/campus_customs_new.db`, refuses to reset while a workflow is active or SQLite sidecars are present, and preserves/appends the audit trail. It does not clear the separate human-decision file. On a reused local checkout, review/retain prior decisions and audit records deliberately; on a fresh clone, the ignored decision file starts absent. Resetting is destructive to the current working-database results, so do it only when intentionally beginning a fresh full run. Never edit the original database.

Then use the dashboard to run the tickets. Agents may prepare payment requests, but pause for a person to approve/reject each one and record any requested human decision in the dashboard. The final disposition distinguishes operational resolution from physical delivery; for example, Ticket 101 is resolved with **Awaiting Restock**, not delivered.

## Submission evidence

`output/harness.md` documents the database schema, agents, MCP tools, API routes, dashboard, and safety rules. `output/mcp_smoke.json` records the MCP smoke results; `output/desk_tickets.html` includes Expected/Actual workflows, Cash, and Reflection; `output/resolved_tickets.json` and `output/resolved_board.html` preserve the Problem 9 evidence. See `AI_prompts.md` for the prompt log. The final public repository link is recorded in `output/github_url.txt`.
