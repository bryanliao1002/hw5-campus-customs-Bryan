export interface Ticket {
  id: number;
  type: string;
  requester: string;
  subject: string;
  sku: string | null;
  size: string | null;
  qty: number | null;
  lease_id: number | null;
  invoice_id: number | null;
  status: string;
  notes: string | null;
  created_at: string;
  is_open: boolean;
  is_resolved: boolean;
}

export interface CashAccount {
  name: string;
  balance: number;
  date: string;
}

export interface ApprovalRequest {
  request_id: number;
  ticket_id: number;
  amount: number;
  account: string;
  status: string;
  requested_at: string;
  ticket_subject: string;
  kind?: string;
  ref_id?: number;
  sku?: string;
  size?: string;
  qty?: number;
  vendor_id?: number;
  vendor_name?: string;
  unit_cost?: number;
}

export interface ApprovalQueue {
  payments: ApprovalRequest[];
  purchases: ApprovalRequest[];
}

export interface HumanDecision {
  decision_id: string;
  ticket_id: number;
  kind: "fulfillment" | "discount" | "other";
  question: string;
  recommendation: string;
  options: string[];
  status: "pending" | "decided";
  created_at: string;
  requested_by: string;
  decided_by: string | null;
  choice: string | null;
  terms: string | null;
  decided_at: string | null;
}

export interface ActivityEvent {
  timestamp: string | null;
  event: string;
  workflow_id: string | null;
  ticket_id: number | null;
  agent: string | null;
  delegated_to: string | null;
  message: string;
  agent_output: {
    summary?: string;
    findings?: string[];
    decisions?: string[];
    next_steps?: string[];
    status?: string;
  } | null;
  mcp_tool: string | null;
  tool_inputs: Record<string, unknown> | null;
  tool_result: unknown;
  status: string | null;
  error: string | null;
  usage: { requests?: number; tool_calls?: number; total_tokens?: number } | null;
}

export interface TicketRun {
  run_id: string;
  ticket_id: number;
  status: string;
  synthesis_recovery?: boolean;
  summary?: string;
  error?: string;
  agent_calls?: number;
  tokens_used?: number;
  result?: {
    summary?: string;
    boss_report?: ActivityEvent["agent_output"];
  };
}

export interface TicketContinuation {
  ticket_id: number;
  eligible: boolean;
  reason: string;
  continuation_of?: string;
  processed_payments?: Array<{
    request_id: number;
    payment_id: number;
    kind: string;
    ref_id: number;
    amount: number;
  }>;
}

export interface SynthesisRecoveryCandidate {
  ticket_id: number;
  eligible: boolean;
  in_progress?: boolean;
  reason: string;
  failed_workflow_id?: string;
  saved_specialists?: string[];
}

export interface ActivityResponse {
  activity: ActivityEvent[];
  count: number;
}

export interface DashboardSnapshot {
  tickets: Ticket[];
  cash: CashAccount;
  approvals: ApprovalQueue;
  activity: ActivityEvent[];
}
