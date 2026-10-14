import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, api, type ApprovalItem } from "./api";
import type {
  ActivityEvent,
  ApprovalQueue,
  CashAccount,
  HumanDecision,
  Ticket,
  TicketContinuation,
  TicketRun,
  SynthesisRecoveryCandidate,
} from "./types";

type AgentRole = "boss" | "inventory" | "accounting" | "facilities" | "customer_service";
type RunMap = Record<number, TicketRun>;

const agents: { id: AgentRole; label: string; initials: string; focus: string }[] = [
  { id: "boss", label: "Boss", initials: "BO", focus: "Triage & decisions" },
  { id: "inventory", label: "Inventory", initials: "IN", focus: "Stock & suppliers" },
  { id: "accounting", label: "Accounting", initials: "AC", focus: "Cash & margins" },
  { id: "facilities", label: "Facilities", initials: "FA", focus: "Lease & space" },
  { id: "customer_service", label: "Customer Service", initials: "CS", focus: "Message drafts" },
];

const roleLabels: Record<string, string> = Object.fromEntries(agents.map((agent) => [agent.id, agent.label]));

function money(amount?: number) {
  if (amount === undefined || !Number.isFinite(amount)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(amount);
}

function ticketKind(type: string) {
  return type.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function timeLabel(value: string | null) {
  if (!value) return "Just now";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(date);
}

function displayStatus(ticket: Ticket, run: TicketRun | undefined, locallyResolved: boolean) {
  if (ticket.is_resolved || locallyResolved || run?.status === "complete") return { label: "Resolved", tone: "resolved" };
  if (run?.status === "queued" || run?.status === "running") return { label: "In progress", tone: "active" };
  if (run?.status === "needs_human") return { label: "Needs review", tone: "review" };
  if (run?.status === "blocked" || run?.status === "failed") return { label: run.status === "failed" ? "Run failed" : "Blocked", tone: "blocked" };
  return { label: ticket.status === "open" ? "Open" : ticket.status, tone: "open" };
}

function isAwaitingRestock(decisions: HumanDecision[], ticketId: number) {
  const latestFulfillment = decisions
    .filter((decision) => decision.ticket_id === ticketId && decision.kind === "fulfillment" && decision.status === "decided")
    .sort((left, right) => (right.decided_at ?? "").localeCompare(left.decided_at ?? ""))[0];
  if (!latestFulfillment?.choice || !latestFulfillment.terms) return false;

  const choice = latestFulfillment.choice.toLowerCase();
  const terms = latestFulfillment.terms.toLowerCase();
  return choice.includes("defer") && choice.includes("restock") && terms.includes("defer fulfillment") && terms.includes("confirmed available");
}

function EventIcon({ event }: { event: ActivityEvent }) {
  if (event.mcp_tool) return <span className="event-symbol tool-symbol">⌘</span>;
  if (event.event === "agent_delegation") return <span className="event-symbol delegate-symbol">↗</span>;
  if (event.event.includes("failed")) return <span className="event-symbol failed-symbol">!</span>;
  return <span className="event-symbol agent-symbol">✳</span>;
}

function ApprovalCard({ item, onApprove, onReject }: { item: ApprovalItem; onApprove: (item: ApprovalItem) => void; onReject: (item: ApprovalItem) => void }) {
  const isPurchase = item.requestType === "purchase";
  return (
    <article className="approval-card">
      <div className="approval-card__top">
        <span className={`approval-kind ${isPurchase ? "approval-kind--purchase" : ""}`}>
          {isPurchase ? "Purchase proposal" : `${item.kind ?? "Payment"} payment`}
        </span>
        <span className="approval-amount">{money(item.amount)}</span>
      </div>
      <h3>{item.ticket_subject || `Ticket #${item.ticket_id}`}</h3>
      <p className="approval-detail">
        {isPurchase
          ? `${item.qty ?? "?"} × ${item.sku ?? "item"} / ${item.size ?? "—"} · ${item.vendor_name ?? "Supplier pending"}`
          : `Reference #${item.ref_id ?? "—"} · ${item.account} account`}
      </p>
      <button className="button button--approve" onClick={() => onApprove(item)}>
        Review & approve <span aria-hidden="true">↗</span>
      </button>
      <button className="button button--reject" onClick={() => onReject(item)}>Reject proposal</button>
    </article>
  );
}

function HumanDecisionCard({ decision, onSubmit }: {
  decision: HumanDecision;
  onSubmit: (decision: HumanDecision, submission: { decided_by: string; choice: string; terms: string }) => Promise<void>;
}) {
  const [decidedBy, setDecidedBy] = useState("");
  const [choice, setChoice] = useState("");
  const [terms, setTerms] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <article className="decision-card">
      <div className="decision-card__top"><span className="approval-kind">{decision.kind} · Ticket #{decision.ticket_id}</span><time>{timeLabel(decision.created_at)}</time></div>
      <h3>{decision.question}</h3>
      <p><strong>Agent recommendation:</strong> {decision.recommendation}</p>
      <div className="decision-options" aria-label="Decision choices">
        {decision.options.map((option) => <button key={option} className={`decision-option ${choice === option ? "is-selected" : ""}`} onClick={() => setChoice(option)}>{option}</button>)}
      </div>
      <label className="field-label" htmlFor={`decision-terms-${decision.decision_id}`}>Terms / conditions</label>
      <textarea id={`decision-terms-${decision.decision_id}`} className="decision-terms" value={terms} onChange={(event) => setTerms(event.target.value)} placeholder="Record any conditions, timing caveats, or customer handling terms." />
      <label className="field-label" htmlFor={`decision-by-${decision.decision_id}`}>Human decision-maker</label>
      <input id={`decision-by-${decision.decision_id}`} className="text-input" value={decidedBy} onChange={(event) => setDecidedBy(event.target.value)} placeholder="Name or staff identifier" />
      <div className="decision-card__footer"><span>Requested by {decision.requested_by}; agents cannot record this choice.</span><button className="button button--approve" disabled={busy || !choice || !terms.trim() || !decidedBy.trim()} onClick={() => { setBusy(true); void onSubmit(decision, { decided_by: decidedBy.trim(), choice, terms: terms.trim() }).catch(() => undefined).finally(() => setBusy(false)); }}>{busy ? "Recording…" : "Record human decision"}</button></div>
    </article>
  );
}

function FinancialRejectDialog({ item, onClose, onSubmit }: {
  item: ApprovalItem;
  onClose: () => void;
  onSubmit: (item: ApprovalItem, rejectedBy: string, reason: string) => Promise<void>;
}) {
  const [rejectedBy, setRejectedBy] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="modal-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="approval-dialog" role="dialog" aria-modal="true" aria-labelledby="reject-title">
        <button className="icon-button dialog-close" onClick={onClose} aria-label="Close rejection dialog">×</button>
        <div className="dialog-mark dialog-mark--reject">×</div><p className="eyebrow">Human review</p>
        <h2 id="reject-title">Reject this proposal?</h2><p className="dialog-summary">{item.ticket_subject} · {money(item.amount)}. No cash will leave the business.</p>
        <label className="field-label" htmlFor="reject-by">Reviewer name</label><input id="reject-by" className="text-input" value={rejectedBy} onChange={(event) => setRejectedBy(event.target.value)} placeholder="Name or staff identifier" />
        <label className="field-label rejection-reason-label" htmlFor="reject-reason">Reason</label><textarea id="reject-reason" className="decision-terms" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why is this proposal being rejected?" />
        <div className="dialog-actions"><button className="button button--quiet" onClick={onClose}>Cancel</button><button className="button button--reject-confirm" disabled={busy || !rejectedBy.trim() || !reason.trim()} onClick={() => { setBusy(true); void onSubmit(item, rejectedBy.trim(), reason.trim()).catch(() => undefined).finally(() => setBusy(false)); }}>{busy ? "Recording…" : "Confirm rejection"}</button></div>
      </section>
    </div>
  );
}

function ApprovalDialog({
  item,
  busy,
  onClose,
  onSubmit,
}: {
  item: ApprovalItem;
  busy: boolean;
  onClose: () => void;
  onSubmit: (approver: string, vendorConfirmed: boolean) => void;
}) {
  const [approver, setApprover] = useState("");
  const [vendorConfirmed, setVendorConfirmed] = useState(false);
  const purchase = item.requestType === "purchase";
  return (
    <div className="modal-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="approval-dialog" role="dialog" aria-modal="true" aria-labelledby="approval-title">
        <button className="icon-button dialog-close" onClick={onClose} aria-label="Close approval dialog">×</button>
        <div className="dialog-mark">✓</div>
        <p className="eyebrow">Human decision required</p>
        <h2 id="approval-title">Approve {purchase ? "purchase" : "payment"}?</h2>
        <p className="dialog-summary">
          {item.ticket_subject} <span>·</span> {money(item.amount)} from {item.account}.
          Approval processes the recorded request and updates the checking balance.
        </p>
        <label className="field-label" htmlFor="approver-name">Approver name</label>
        <input
          id="approver-name"
          className="text-input"
          value={approver}
          onChange={(event) => setApprover(event.target.value)}
          placeholder="Name or staff identifier"
          autoFocus
        />
        {purchase && (
          <label className="confirm-line">
            <input type="checkbox" checked={vendorConfirmed} onChange={(event) => setVendorConfirmed(event.target.checked)} />
            <span>I have independently confirmed this vendor can supply the exact SKU and size.</span>
          </label>
        )}
        <div className="dialog-safety"><span>↳</span> The agents prepared this request; only a human can authorize it.</div>
        <div className="dialog-actions">
          <button className="button button--quiet" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            className="button button--approve"
            disabled={busy || approver.trim().length === 0 || (purchase && !vendorConfirmed)}
            onClick={() => onSubmit(approver.trim(), vendorConfirmed)}
          >
            {busy ? "Processing…" : "Confirm approval"}
          </button>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [cash, setCash] = useState<CashAccount | null>(null);
  const [approvals, setApprovals] = useState<ApprovalQueue>({ payments: [], purchases: [] });
  const [decisions, setDecisions] = useState<HumanDecision[]>([]);
  const [recordedDecisions, setRecordedDecisions] = useState<HumanDecision[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [runs, setRuns] = useState<RunMap>({});
  const [continuation, setContinuation] = useState<TicketContinuation | null>(null);
  const [synthesisRecovery, setSynthesisRecovery] = useState<SynthesisRecoveryCandidate | null>(null);
  const [recoveryCheckReady, setRecoveryCheckReady] = useState(false);
  const runsRef = useRef(runs);
  runsRef.current = runs;
  const [resolvedFromAudit, setResolvedFromAudit] = useState<Set<number>>(new Set());
  const [selectedId, setSelectedId] = useState(101);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [approvalTarget, setApprovalTarget] = useState<ApprovalItem | null>(null);
  const [rejectionTarget, setRejectionTarget] = useState<ApprovalItem | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [startBusy, setStartBusy] = useState<number | null>(null);

  const reload = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    try {
      const [ticketData, cashData, approvalData, decisionData, activityData] = await Promise.all([
        api.tickets(), api.checking(), api.approvals(), api.decisions(), api.activity({ limit: 200 }),
      ]);
      setTickets(ticketData.tickets);
      setCash(cashData);
      setApprovals(approvalData);
      setDecisions(decisionData.decisions.filter((decision) => decision.status === "pending"));
      setRecordedDecisions(decisionData.decisions.filter((decision) => decision.status === "decided"));
      setActivity(activityData.activity);
      setError("");
      setConnected(true);

      let lastReset = -1;
      activityData.activity.forEach((item, index) => {
        if (item.event === "api_database_reset") lastReset = index;
      });
      const completed = new Set<number>();
      activityData.activity.slice(lastReset + 1).forEach((item) => {
        if (item.event === "api_ticket_run_finished" && item.status === "complete" && item.ticket_id != null) {
          completed.add(item.ticket_id);
        }
      });
      setResolvedFromAudit(completed);
      if (!ticketData.tickets.some((ticket) => ticket.id === selectedId)) {
        setSelectedId(ticketData.tickets[0]?.id ?? 101);
      }
    } catch (caught) {
      setConnected(false);
      setError(caught instanceof Error ? caught.message : "The dashboard couldn't load its data.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [selectedId]);

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => void reload(true), 7000);
    return () => window.clearInterval(timer);
  }, [reload]);

  useEffect(() => {
    let cancelled = false;
    setContinuation(null);
    setSynthesisRecovery(null);
    setRecoveryCheckReady(false);
    void api.continuation(selectedId).then((state) => {
      if (!cancelled) setContinuation(state);
    }).catch(() => {
      if (!cancelled) setContinuation(null);
    });
    const checkRecovery = () => {
      void api.synthesisRecoveryCandidate(selectedId).then((state) => {
        if (!cancelled) {
          setSynthesisRecovery(state);
          setRecoveryCheckReady(true);
        }
      }).catch(() => {
        if (!cancelled) {
          setSynthesisRecovery(null);
          setRecoveryCheckReady(false);
        }
      });
    };
    checkRecovery();
    const interval = window.setInterval(checkRecovery, 7000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [selectedId]);

  const runningSignature = useMemo(
    () => Object.values(runs).filter((run) => run.status === "queued" || run.status === "running").map((run) => run.run_id).join("|"),
    [runs],
  );

  useEffect(() => {
    if (!runningSignature) return;
    const poll = async () => {
      const active = Object.values(runsRef.current).filter((run) => run.status === "queued" || run.status === "running");
      await Promise.all(active.map(async (run) => {
        try {
          const [freshRun, freshActivity] = await Promise.all([
            api.run(run.run_id),
            api.activity({ ticketId: run.ticket_id, workflowId: run.run_id, limit: 200 }),
          ]);
          setRuns((previous) => ({ ...previous, [run.ticket_id]: freshRun }));
          setActivity((previous) => {
            const unique = new Map<string, ActivityEvent>();
            [...previous, ...freshActivity.activity].forEach((item) => {
              unique.set(`${item.workflow_id ?? ""}:${item.timestamp ?? ""}:${item.event}:${item.agent ?? item.mcp_tool ?? ""}`, item);
            });
            return [...unique.values()].slice(-240);
          });
          if (!["queued", "running"].includes(freshRun.status)) {
            if (freshRun.status === "complete") {
              setResolvedFromAudit((previous) => new Set(previous).add(run.ticket_id));
              setToast(`Ticket #${run.ticket_id} is resolved.`);
            } else {
              setToast(`Ticket #${run.ticket_id}: ${freshRun.status.replaceAll("_", " ")}.`);
            }
            void reload(true);
          }
        } catch (caught) {
          const message = caught instanceof Error ? caught.message : "Run status could not be refreshed.";
          setRuns((previous) => ({ ...previous, [run.ticket_id]: { ...run, status: "failed", error: message } }));
        }
      }));
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1800);
    return () => window.clearInterval(timer);
  }, [runningSignature, reload]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const selectedTicket = tickets.find((ticket) => ticket.id === selectedId) ?? null;
  const selectedRun = runs[selectedId];
  const status = selectedTicket ? displayStatus(selectedTicket, selectedRun, resolvedFromAudit.has(selectedId)) : null;
  const awaitingRestock = Boolean(status?.tone === "resolved" && selectedTicket && isAwaitingRestock(recordedDecisions, selectedTicket.id));
  const activeRuns = Object.values(runs).filter((run) => run.status === "queued" || run.status === "running").length;
  const recoverySpecialists = (synthesisRecovery?.saved_specialists ?? [])
    .map((role) => roleLabels[role] ?? role)
    .join(", ");
  const approvalItems: ApprovalItem[] = [
    ...approvals.payments.map((item) => ({ ...item, requestType: "payment" as const })),
    ...approvals.purchases.map((item) => ({ ...item, requestType: "purchase" as const })),
  ];

  const ticketActivity = useMemo(() => activity
    .filter((item) => item.ticket_id === selectedId && (!selectedRun?.run_id || item.workflow_id === selectedRun.run_id || !item.workflow_id))
    .slice(-60)
    .reverse(), [activity, selectedId, selectedRun?.run_id]);

  const latestByAgent = useMemo(() => {
    const latest = new Map<string, ActivityEvent>();
    ticketActivity.filter((item) => item.event === "agent_run_completed" && item.agent)
      .reverse()
      .forEach((item) => latest.set(item.agent!, item));
    return latest;
  }, [ticketActivity]);

  const handleStart = async (ticketId: number) => {
    setError("");
    setStartBusy(ticketId);
    try {
      const run = await api.startTicket(ticketId);
      setRuns((previous) => ({ ...previous, [ticketId]: run }));
      setSelectedId(ticketId);
      setToast(`Boss is reviewing ticket #${ticketId}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Couldn't start this ticket.");
    } finally {
      setStartBusy(null);
    }
  };

  const handleContinue = async (ticketId: number) => {
    setError("");
    setStartBusy(ticketId);
    try {
      const run = await api.continueTicket(ticketId);
      setRuns((previous) => ({ ...previous, [ticketId]: run }));
      setSelectedId(ticketId);
      setContinuation({ ticket_id: ticketId, eligible: false, reason: "Continuation has started." });
      setToast(`Continuing ticket #${ticketId} after the recorded human payment.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Couldn't continue this ticket.");
      const fresh = await api.continuation(ticketId).catch(() => null);
      setContinuation(fresh);
    } finally {
      setStartBusy(null);
    }
  };

  const handleRecoverBossSynthesis = async (ticketId: number) => {
    const failedWorkflowId = synthesisRecovery?.failed_workflow_id;
    if (!failedWorkflowId) return;
    setError("");
    setStartBusy(ticketId);
    try {
      const run = await api.recoverBossSynthesis(ticketId, failedWorkflowId);
      setRuns((previous) => ({ ...previous, [ticketId]: run }));
      setSynthesisRecovery({
        ticket_id: ticketId,
        eligible: false,
        reason: "Boss is reviewing the saved specialist evidence.",
      });
      setToast("Boss synthesis resumed. Completed specialists will not be rerun.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Boss synthesis could not be resumed.");
      const fresh = await api.synthesisRecoveryCandidate(ticketId).catch(() => null);
      setSynthesisRecovery(fresh);
    } finally {
      setStartBusy(null);
    }
  };

  const handleApproval = async (approver: string, vendorConfirmed: boolean) => {
    if (!approvalTarget) return;
    setApprovalBusy(true);
    setError("");
    const approvedTicketId = approvalTarget.ticket_id;
    try {
      const result = await api.approve(approvalTarget.requestType, approvalTarget.request_id, {
        approved_by: approver,
        confirm: true,
        confirm_vendor_can_supply: vendorConfirmed,
      });
      setApprovalTarget(null);
      setSelectedId(approvedTicketId);
      setToast(`Approved · ${money(result.amount)} processed. Checking is now ${money(result.new_balance)}.`);
      await reload(true);
      const continuationState = await api.continuation(approvedTicketId).catch(() => null);
      setContinuation(continuationState);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Approval wasn't processed.");
    } finally {
      setApprovalBusy(false);
    }
  };

  const handleDecision = async (decision: HumanDecision, submission: { decided_by: string; choice: string; terms: string }) => {
    setError("");
    try {
      const result = await api.decide(decision.decision_id, submission);
      const disposition = result.final_disposition;
      setToast(disposition.status === "resolved"
        ? `Ticket #${decision.ticket_id} resolved after the recorded human disposition.`
        : `Decision recorded. Ticket #${decision.ticket_id} remains open${disposition.outstanding?.length ? `: ${disposition.outstanding.join("; ")}` : " pending final checks"}.`);
      setSelectedId(decision.ticket_id);
      await reload(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The human decision could not be recorded.");
      throw caught;
    }
  };

  const handleFinancialRejection = async (item: ApprovalItem, rejectedBy: string, reason: string) => {
    try {
      await api.reject(item.requestType, item.request_id, { rejected_by: rejectedBy, reason });
      setRejectionTarget(null);
      setToast(`Proposal for ticket #${item.ticket_id} was rejected; no payment was made.`);
      await reload(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The proposal could not be rejected.");
      throw caught;
    }
  };

  return (
    <div className="app-shell">
      <div className="ambient ambient--top" />
      <div className="ambient ambient--lower" />
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><span>CC</span><i /></div>
          <div>
            <div className="brand-name">CAMPUS <span>CUSTOMS</span></div>
            <div className="brand-subtitle">MULTI-AGENT OPERATIONS</div>
          </div>
        </div>
        <div className="topbar-right">
          <div className={`connection-pill ${connected ? "is-connected" : connected === false ? "is-offline" : ""}`}>
            <span className="connection-dot" />
            {connected ? "API connected" : connected === false ? "API offline" : "Connecting"}
          </div>
          <span className="api-endpoint" title={API_BASE_URL}>{API_BASE_URL.replace("http://", "")}</span>
          <button className="icon-button refresh-button" onClick={() => void reload(true)} aria-label="Refresh dashboard" title="Refresh dashboard">
            <span className={refreshing ? "spin" : ""}>↻</span>
          </button>
          <div className="operator-chip"><span className="operator-avatar">M</span><span>Manager</span><span className="operator-caret">⌄</span></div>
        </div>
      </header>

      <main className="dashboard">
        <section className="welcome-row">
          <div>
            <p className="eyebrow"><span className="live-star">✳</span> THE CAMPUS CUSTOMS CONTROL ROOM</p>
            <h1>Good work, <span>in motion.</span></h1>
            <p className="welcome-copy">One small shop. A very capable crew. Keep every decision in view.</p>
          </div>
          <div className="date-chip">
            <span className="date-icon">◷</span>
            <span><b>SHOP DATE</b><strong>{cash?.date ?? "—"}</strong></span>
          </div>
        </section>

        {error && <div className="alert-banner" role="alert"><span>!</span><div>{error}</div><button onClick={() => setError("")} aria-label="Dismiss error">×</button></div>}

        <section className="overview-grid" aria-label="Operations overview">
          <div className="overview-card overview-card--tickets">
            <div className="overview-icon">▤</div>
            <div><span className="overview-label">OPEN TICKETS</span><strong>{tickets.filter((ticket) => displayStatus(ticket, runs[ticket.id], resolvedFromAudit.has(ticket.id)).tone !== "resolved").length.toString().padStart(2, "0")}</strong></div>
            <span className="overview-foot">of {tickets.length.toString().padStart(2, "0")} on the board</span>
          </div>
          <div className="overview-card overview-card--active">
            <div className="overview-icon">◌</div>
            <div><span className="overview-label">AGENT RUNS</span><strong>{activeRuns.toString().padStart(2, "0")}</strong></div>
            <span className="overview-foot">{activeRuns ? "working now" : "crew standing by"}</span>
          </div>
          <div className="overview-card overview-card--approvals">
            <div className="overview-icon">✳</div>
            <div><span className="overview-label">NEEDS YOUR EYE</span><strong>{(approvalItems.length + decisions.length).toString().padStart(2, "0")}</strong></div>
            <span className="overview-foot">approvals & decisions</span>
          </div>
          <div className="cash-overview">
            <div className="cash-copy"><span className="overview-label">CHECKING BALANCE</span><span className="cash-value">{money(cash?.balance)}</span><span className="cash-foot">Recorded checking cash <i>↗</i></span></div>
            <div className="cash-orbit" aria-hidden="true"><span>$</span><i /><b /></div>
          </div>
        </section>

        <div className="workbench-heading">
          <div><span className="section-kicker">01 / WORKBENCH</span><h2>Ticket desk</h2></div>
          <div className="workbench-hint"><span className="key-dot" /> Select a ticket to open its live brief</div>
        </div>

        <section className="workbench-grid">
          <aside className="ticket-rail" aria-label="Ticket list">
            <div className="rail-heading"><span>OPEN BOARD</span><span className="rail-count">{tickets.length}</span></div>
            <div className="ticket-list">
              {loading && tickets.length === 0 ? <div className="skeleton-ticket" /> : tickets.map((ticket, index) => {
                const ticketStatus = displayStatus(ticket, runs[ticket.id], resolvedFromAudit.has(ticket.id));
                return (
                  <button key={ticket.id} className={`ticket-tab ${selectedId === ticket.id ? "is-selected" : ""}`} onClick={() => setSelectedId(ticket.id)}>
                    <span className={`ticket-number ticket-number--${index + 1}`}>{String(ticket.id).slice(-2)}</span>
                    <span className="ticket-tab-copy"><span className="ticket-tab-title">{ticket.subject}</span><span className="ticket-tab-requester">{ticket.requester}</span></span>
                    <span className={`mini-status mini-status--${ticketStatus.tone}`} title={ticketStatus.label} />
                    {selectedId === ticket.id && <span className="ticket-tab-arrow">↗</span>}
                  </button>
                );
              })}
            </div>
            <div className="rail-divider" />
            <div className="crew-label">YOUR CREW <span>5 READY</span></div>
            <div className="crew-mini-list">
              {agents.map((agent) => <div className="crew-mini" key={agent.id}><span className={`agent-avatar agent-${agent.id}`}>{agent.initials}</span><span>{agent.label}</span><i className="ready-dot" /></div>)}
            </div>
            <div className="rail-note"><span>✳</span><p>Agents can recommend and prepare. <b>You keep the final say.</b></p></div>
          </aside>

          <section className="ticket-main" aria-live="polite">
            {!selectedTicket ? (
              <div className="empty-state"><span>✳</span><h3>{loading ? "Opening the board…" : "No tickets found"}</h3><p>{loading ? "Connecting to Campus Customs." : "The API returned an empty ticket list."}</p></div>
            ) : <>
              <div className="ticket-main-top">
                <div className="ticket-main-title">
                  <div className="ticket-meta"><span className="ticket-id">TICKET / {selectedTicket.id}</span><span className="meta-separator">·</span><span>{ticketKind(selectedTicket.type)}</span></div>
                  <h2>{selectedTicket.subject}</h2>
                  <p className="requester-line">From <strong>{selectedTicket.requester}</strong> <span>·</span> opened {selectedTicket.created_at.slice(0, 10)}</p>
                </div>
                {status && <div className="ticket-status-group">
                  <span className={`status-pill status-pill--${status.tone}`}><i />{status.label}</span>
                  {awaitingRestock && <span className="status-pill status-pill--restock" title="Human decision: defer fulfillment until the exact item is confirmed available."><i />Awaiting Restock</span>}
                </div>}
              </div>
              <div className="ticket-facts">
                {selectedTicket.sku && <span><b>SKU</b>{selectedTicket.sku}</span>}
                {selectedTicket.size && <span><b>SIZE</b>{selectedTicket.size}</span>}
                {selectedTicket.qty != null && <span><b>QTY</b>{selectedTicket.qty}</span>}
                {selectedTicket.lease_id != null && <span><b>LEASE</b>#{selectedTicket.lease_id}</span>}
                {selectedTicket.invoice_id != null && <span><b>INVOICE</b>#{selectedTicket.invoice_id}</span>}
              </div>
              {selectedTicket.notes && <div className="ticket-note"><span className="note-mark">“</span><p>{selectedTicket.notes}</p><span className="note-caption">TICKET NOTE</span></div>}

              <div className="run-control">
                <div className="run-control-copy"><span className="run-spark">✳</span><div><strong>{selectedRun?.status === "queued" || selectedRun?.status === "running" ? (selectedRun.synthesis_recovery ? "Boss is finishing the review" : "The crew is on it") : selectedRun?.status === "complete" ? "Work complete" : synthesisRecovery?.in_progress ? "Boss recovery is already queued" : synthesisRecovery?.eligible ? "Saved specialist work is available" : !recoveryCheckReady ? "Checking saved workflow evidence" : continuation?.eligible ? "Human payment verified" : "Ready for the crew?"}</strong><span>{synthesisRecovery?.in_progress ? "A Boss-only recovery request is already in the audit trail. Completed specialists will not be rerun." : synthesisRecovery?.eligible ? `${synthesisRecovery.reason} ${recoverySpecialists || "Completed specialists"} will not be rerun.` : !recoveryCheckReady ? "Verifying the existing run before allowing another agent start." : continuation?.eligible ? continuation.reason : selectedRun?.summary ?? selectedRun?.error ?? "Boss will review this ticket and bring in the right specialists."}</span></div></div>
                <button
                  className="button button--primary"
                  onClick={() => void (synthesisRecovery?.eligible && synthesisRecovery.failed_workflow_id
                    ? handleRecoverBossSynthesis(selectedTicket.id)
                    : continuation?.eligible ? handleContinue(selectedTicket.id) : handleStart(selectedTicket.id))}
                  disabled={startBusy !== null || activeRuns > 0 || selectedTicket.is_resolved || resolvedFromAudit.has(selectedTicket.id) || selectedRun?.status === "complete" || !connected || !recoveryCheckReady || !synthesisRecovery || synthesisRecovery.in_progress}
                >
                  {startBusy === selectedTicket.id || selectedRun?.status === "queued" || selectedRun?.status === "running" ? <><span className="button-spinner" /> Working</> : selectedRun?.status === "complete" ? <>Resolved <span>✓</span></> : synthesisRecovery?.in_progress ? <>Recovery queued</> : !recoveryCheckReady ? <>Verifying…</> : synthesisRecovery?.eligible ? <>Resume Boss synthesis <span>↗</span></> : continuation?.eligible ? <>Continue agents <span>↗</span></> : <>Start agents <span>↗</span></>}
                </button>
              </div>

              <div className="section-row"><div><span className="section-kicker">02 / LIVE SIGNAL</span><h3>Agent activity</h3></div><span className="live-label"><i /> LIVE FEED</span></div>
              <div className="activity-feed">
                {ticketActivity.length === 0 ? <div className="feed-empty"><span>✳</span><div><strong>Quiet for now</strong><p>Agent notes, handoffs, and MCP tool calls will appear here when work begins.</p></div></div> : ticketActivity.slice(0, 12).map((event, index) => {
                  const role = event.agent ?? "system";
                  const roleClass = agents.some((agent) => agent.id === role) ? `agent-${role}` : "agent-boss";
                  const message = event.agent_output?.summary ?? event.message;
                  return (
                    <article className="feed-event" key={`${event.timestamp}-${event.event}-${index}`}>
                      <div className={`feed-avatar agent-${roleClass.replace("agent-", "")}`}>{roleLabels[role] ? agents.find((agent) => agent.id === role)?.initials : event.mcp_tool ? "⌘" : "CC"}</div>
                      <div className="feed-event-main">
                        <div className="feed-event-head"><strong>{roleLabels[role] ?? (event.mcp_tool ? "MCP tool" : event.delegated_to ? `${roleLabels[role] ?? role} → ${roleLabels[event.delegated_to] ?? event.delegated_to}` : "Operations")}</strong><time>{timeLabel(event.timestamp)}</time></div>
                        <p>{message}</p>
                        {event.delegated_to && <span className="event-tag">HANDOFF · {roleLabels[event.delegated_to] ?? event.delegated_to}</span>}
                        {event.mcp_tool && <span className="tool-chip"><b>TOOL</b>{event.mcp_tool}</span>}
                        {event.error && <span className="event-error">{event.error}</span>}
                      </div>
                      <EventIcon event={event} />
                    </article>
                  );
                })}
              </div>

              <div className="section-row contribution-heading"><div><span className="section-kicker">03 / CREW NOTES</span><h3>What each agent contributed</h3></div></div>
              <div className="contribution-grid">
                {agents.map((agent) => {
                  const contribution = latestByAgent.get(agent.id);
                  const output = contribution?.agent_output;
                  const summary = output?.summary ?? (contribution ? contribution.message : "No contribution yet");
                  return (
                    <article className={`contribution-card contribution-card--${agent.id}`} key={agent.id}>
                      <div className="contribution-head"><span className={`agent-avatar agent-${agent.id}`}>{agent.initials}</span><div><strong>{agent.label}</strong><span>{agent.focus}</span></div><i className={contribution ? "contribution-dot is-active" : "contribution-dot"} /></div>
                      <p>{summary}</p>
                      {output?.findings?.length ? <ul>{output.findings.slice(0, 2).map((finding, index) => <li key={index}>{finding}</li>)}</ul> : null}
                    </article>
                  );
                })}
              </div>
            </>}
          </section>
        </section>

        <section className="approval-section" id="approvals">
          <div className="approval-section-heading"><div><span className="section-kicker">04 / HUMAN CHECKPOINT</span><h2>Financial proposals <span className="approval-count">{approvalItems.length}</span></h2><p>Money moves only after approval; rejection records a reason without changing cash.</p></div><div className="approval-lock">⌑ <span>HUMAN-ONLY ACTION</span></div></div>
          {approvalItems.length === 0 ? (
            <div className="approval-empty"><div className="approval-empty-icon">✓</div><div><strong>All clear for now</strong><p>No payment or purchase requests are waiting in the approval queue.</p></div><span className="approval-empty-mark">CAMPUS CUSTOMS · FINANCE DESK</span></div>
          ) : <div className="approval-grid">{approvalItems.map((item) => <ApprovalCard key={`${item.requestType}-${item.request_id}`} item={item} onApprove={setApprovalTarget} onReject={setRejectionTarget} />)}</div>}
        </section>

        <section className="decision-section" id="human-decisions">
          <div className="approval-section-heading"><div><span className="section-kicker">05 / OPERATIONAL DISPOSITION</span><h2>Human decisions <span className="approval-count">{decisions.length}</span></h2><p>Choose an option and record terms. For an out-of-stock order, resolved means its blocking disposition was handled—not that goods were delivered.</p></div><div className="approval-lock">✳ <span>HUMAN-ONLY ACTION</span></div></div>
          {decisions.length ? <div className="decision-grid">{decisions.map((decision) => <HumanDecisionCard key={decision.decision_id} decision={decision} onSubmit={handleDecision} />)}</div> : <div className="approval-empty"><div className="approval-empty-icon">✓</div><div><strong>No operational decisions are waiting.</strong><p>When agents need a human disposition, the request and its evidence will appear here.</p></div></div>}
        </section>

        <footer className="footer"><div><span className="brand-mark brand-mark--small">CC</span><span>Campus Customs <i>·</i> Operations desk</span></div><span>Thoughtful decisions, one ticket at a time.</span></footer>
      </main>
      {toast && <div className="toast" role="status"><span>✳</span>{toast}<button onClick={() => setToast("")} aria-label="Dismiss notification">×</button></div>}
      {approvalTarget && <ApprovalDialog item={approvalTarget} busy={approvalBusy} onClose={() => setApprovalTarget(null)} onSubmit={(approver, vendorConfirmed) => void handleApproval(approver, vendorConfirmed)} />}
      {rejectionTarget && <FinancialRejectDialog item={rejectionTarget} onClose={() => setRejectionTarget(null)} onSubmit={handleFinancialRejection} />}
    </div>
  );
}
