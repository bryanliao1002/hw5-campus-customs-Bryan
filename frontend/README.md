# Campus Customs React dashboard

React, Vite, and TypeScript operations desk for the FastAPI backend in `backend/`.

## Run locally

From `frontend/`:

```sh
npm install
npm run dev
```

The dashboard runs at `http://localhost:5173` and uses the FastAPI backend at `http://localhost:8000`. The API base URL is configurable with `VITE_API_BASE_URL`; the checked-in `.env.example` documents the assignment endpoint, while the ignored local `.env.local` selects the same endpoint for this machine. Environment files are ignored; `.env.example` contains no credentials.

The dashboard polls ticket status, checking balance, pending approval requests, continuation eligibility, and recent activity. Selecting “Start agents” calls the per-ticket Problem 7 route; workflow completion is read from the run endpoint and append-only activity. After approval, it presents a separate user-clicked “Continue agents” action only when the backend verifies the paid request against the payment ledger; approval itself never launches an agent run. A successful `complete` disposition is shown as Resolved only when the database ticket is resolved; `needs_human` and `blocked` remain visibly distinct. Approvals require an approver name, and purchase approvals additionally require the human supplier-confirmation checkbox.

The FastAPI CORS configuration allows `http://localhost:5173` and `http://localhost:3000`. The API is local-only for financial approval and reset actions.
