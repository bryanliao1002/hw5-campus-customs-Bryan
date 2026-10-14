import type {
  ActivityResponse,
  ApprovalQueue,
  ApprovalRequest,
  CashAccount,
  HumanDecision,
  Ticket,
  TicketContinuation,
  TicketRun,
  SynthesisRecoveryCandidate,
} from "./types";

const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
export const API_BASE_URL = (configuredBase || "http://localhost:8000").replace(/\/+$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init?.headers);
    if (init?.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers,
    });
  } catch {
    throw new Error(`Can't reach the operations API at ${API_BASE_URL}. Check that FastAPI is running.`);
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
  }
  return payload as T;
}

export const api = {
  tickets: () => request<{ tickets: Ticket[]; count: number }>("/api/tickets"),
  checking: () => request<CashAccount>("/api/cash/checking"),
  approvals: () => request<ApprovalQueue>("/api/approvals"),
  decisions: () => request<{ decisions: HumanDecision[]; count: number }>("/api/decisions"),
  activity: (options: { limit?: number; ticketId?: number; workflowId?: string } = {}) => {
    const query = new URLSearchParams({ limit: String(options.limit ?? 200) });
    if (options.ticketId) query.set("ticket_id", String(options.ticketId));
    if (options.workflowId) query.set("workflow_id", options.workflowId);
    return request<ActivityResponse>(`/api/activity?${query}`);
  },
  startTicket: (ticketId: number) =>
    request<TicketRun>(`/api/tickets/${ticketId}/run`, { method: "POST" }),
  continuation: (ticketId: number) =>
    request<TicketContinuation>(`/api/tickets/${ticketId}/continuation`),
  synthesisRecoveryCandidate: (ticketId: number) =>
    request<SynthesisRecoveryCandidate>(`/api/tickets/${ticketId}/recovery-candidate`),
  continueTicket: (ticketId: number) =>
    request<TicketRun>(`/api/tickets/${ticketId}/continue`, { method: "POST" }),
  recoverBossSynthesis: (ticketId: number, failedWorkflowId: string) =>
    request<TicketRun>(`/api/tickets/${ticketId}/recover-synthesis`, {
      method: "POST",
      body: JSON.stringify({ failed_workflow_id: failedWorkflowId }),
    }),
  run: (runId: string) => request<TicketRun>(`/api/runs/${runId}`),
  approve: (
    kind: "payment" | "purchase",
    requestId: number,
    body: { approved_by: string; confirm: boolean; confirm_vendor_can_supply?: boolean },
  ) =>
    request<{ status: string; amount: number; new_balance: number; payment_id: number }>(
      `/api/approvals/${kind}/${requestId}/approve`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  reject: (kind: "payment" | "purchase", requestId: number, body: { rejected_by: string; reason: string }) =>
    request<{ status: string; ticket_id: number; request_id: number }>(
      `/api/approvals/${kind}/${requestId}/reject`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  decide: (decisionId: string, body: { decided_by: string; choice: string; terms: string }) =>
    request<{ decision: HumanDecision; final_disposition: { status: string; resolved?: boolean; outstanding?: string[] } }>(
      `/api/decisions/${encodeURIComponent(decisionId)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),
};

export type ApprovalKind = "payment" | "purchase";
export type ApprovalItem = ApprovalRequest & { requestType: ApprovalKind };
