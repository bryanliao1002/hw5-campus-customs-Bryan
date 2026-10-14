# HW5 AI Prompt Log

## Problem 1 — AI Prompt Documentation

### My Original Prompt

> Now this is the problem 1: For this problem, I need to create a file called `AI_prompts.md` to document the instructions and questions I give you while working on HW5.
>
> Please create this file now, at the beginning of the project, and continue updating it as we complete each problem. The goal is to keep a record of the actual prompts I send to my AI coding assistant throughout the assignment, word for word.
>
> I want the file to be organized by problem, with a separate section for each one.
>
> Each section should contain:
>
> 1. **Problem Number and Title:** Clearly identify which problem the section is about.
> 2. **My Original Prompt:** Include at least one prompt that I actually sent you for that problem. Keep the wording as close as possible to what I typed, since the assignment wants to see my own explanation rather than copied instructions from the homework.
> 3. **Follow-Up Prompt (If Needed):** If I send another prompt to fix, clarify, or improve something after your first response, record one of those follow-up prompts as well. Also include a short, one-sentence explanation of what was missing or needed improvement after the first attempt.
>
> Please make sure `AI_prompts.md` reflects the prompts I actually used, rather than generating made-up examples.
>
> For now, create the file and add a section for Problem 1, including this prompt. As we move on to the next problems, keep adding the new sections and updating the log when necessary.
>
> Please don't start working on Problem 2 until I provide its instructions.

### Follow-Up Prompt

None so far.

## Problem 2 — Database Schema and Open Ticket Relationships

### My Original Prompt

> Now comes the probem 2: Please examine `data/campus_customs.db` and review all the tables and their fields. Make a copy called `data/campus_customs_new.db`, which we'll use for changes in later problems. Keep the original database unchanged.
> Look into the three open tickets and figure out how each one connects to the other tables.
> Create `output/harness.md` and include every table's fields, along with a short explanation of how each table is useful to the agents.
> We'll continue updating this file as we work through the remaining problems.
> Also, log this prompt in `AI_prompts.md` as required by Problem 1.

### Follow-Up Prompt

None so far.

## Problem 3 — Initial FastMCP Server and Ticket Tools

### My Original Prompt

> Now I am working on problem 3, for this part, I need to build an MCP server in `mcp_server/` using FastMCP. It should use `data/campus_customs_new.db` as the database. We don't need to connect to or run the server just yet.
> Since all five agents will eventually share this MCP server, I think we should start with three tools that are useful for the tickets already in the database. We can add more tools later. Please give each tool a clear name and make sure everything is based on actual database information, without making anything up.
> Also, please update `output/harness.md` to document these three tools. For each one, include which database table it reads, which ticket (101, 102, or 103) it helps with, and a short explanation of why it's useful for that specific ticket. Try to be specific here, since general descriptions like "reads inventory" aren't enough.
> Please also create a short `mcp_server/README.md` explaining what the server does, which database it uses, and what the three tools are for.
> Lastly, remember to add this prompt to `AI_prompts.md` as required by Problem 1. Let's focus on Problem 3 for now.

### Follow-Up Prompt

None so far.

## Problem 4 — Connect the MCP Server to Codex and Smoke-Test Tools

### My Original Prompt

> Now working on the problem 4: For this part, I need to connect the MCP server we built earlier to Codex so it can actually access and use the tools. Please set up the MCP connection for this project and save its JSON configuration in `.mcp.json` at the project root, or in the local MCP configuration location that Codex normally uses.
>
> Once the connection is working, please test all three MCP tools through Codex to make sure they work properly.
>
> Save the test results in `output/mcp_smoke.json`. For each tool, please include:
>
> - The actual prompt I used to ask Codex to call the tool.
> - The name of the tool being tested.
> - The output returned by the tool, making sure the values match the actual data in `data/campus_customs_new.db`.
>
> Please use real tool results rather than making up any test outputs. If something doesn't work, let me know and help fix it.
>
> Also, remember to record this prompt in `AI_prompts.md`.

### Follow-Up Prompts

#### Follow-Up 1

> Looks good! Before we move on, could you quickly confirm that `.codex/config.toml` is included in the project and will be tracked by Git? Also, please make sure `output/mcp_smoke.json` contains the actual prompt, tool name, and real output for all three tools, with no missing information or fabricated results. Don't make any unnecessary changes.

**What needed improvement:** The first response reported the smoke tests as successful but had not yet confirmed the Git tracking status or rechecked the saved results against the database.

#### Follow-Up 2

> Thanks for checking! Since HW5 will eventually be submitted through a public GitHub repository, please fix the Git ignore issue so the `hw5` project files can be tracked properly.
> Make sure this doesn't affect my previous HW4 project or any other homework files.
> Also, please keep virtual environments, API keys, `.env` files, and other sensitive or unnecessary files excluded from Git.
> After fixing this, verify that the required HW5 files, including `.codex/config.toml`, can be tracked. Please don't push anything to GitHub yet. Just make sure everything is ready for the final submission later.

**What needed improvement:** The check found that the parent Git repository's local exclude rule ignored HW5, so the project needed isolated tracking without changing the other homework folders.

#### Follow-Up 3

> pls record those follow up promots into AI_prompts.md

**What needed improvement:** The two earlier Problem 4 follow-up prompts and their explanations had not yet been added to the prompt log.

## Problem 5 — PydanticAI Agents, MCP Tools, and Audit Trail

### My Original Prompt

> Now lets work on problem 5: For this part, I need to build the five Campus Customs agents using PydanticAI: Boss, Inventory, Accounting, Facilities, and Customer Service. Please set up their prompts, models, and agent loops so they can communicate and delegate tasks to any other agent.
> Put each agent's prompt in a separate file under `backend/prompts/`, define the data types in `backend/models.py`, and keep the agent code somewhere under `backend/`. The exact file structure is flexible as long as all five roles are clearly separated. Remember to use only `gpt-6-luna` through Portkey with `PORTKEY_API_KEY` for every agent.
> Please write detailed, natural prompts for each agent in our own words, including the shop rules relevant to their responsibilities. I want the prompts to clearly explain what each agent should do, what decisions they can make, and what restrictions they must follow. Avoid short, generic instructions since more complete prompts will receive higher scores.
> We also need to expand our existing MCP server with any additional tools the agents need to handle the open tickets. Please make sure all shop information comes through the MCP server connected to `data/campus_customs_new.db`. Don't create a separate set of database tools that bypasses MCP.
> Also, set up the agents to keep an ongoing record in `output/audit_trail.json`. Each step of an agent's loop should include enough information for us to review what happened later. Please append new records rather than clearing or overwriting previous ones.
> Update `output/harness.md` to include all five agents and every MCP tool, including the new ones, and specify which database tables each tool uses. Please also add a short safety section covering practical protections for real customers and financial transactions, along with limits to prevent excessive token usage.
> Lastly, update `mcp_server/README.md` so it accurately lists all the MCP tools currently available, and record this prompt in `AI_prompts.md` as required by Problem 1.
> Let's focus on Problem 5 for now and wait before moving on to the next problem.

### Follow-Up Prompt

None so far.

## Problem 6 — Expected Ticket Workflows

### My Original Prompt

> Now moving on to problem 6, for this part, before we connect the backend, I want to plan out how our agent team should handle each of the three open tickets.
>
> Please create `output/desk_tickets.html` as a standalone HTML page that I can open by double-clicking. It should have separate tabs for tickets 101, 102, and 103, plus two additional tabs called Cash and Reflection. We can leave those last two tabs empty or just put a short "Coming later" message for now.
>
> For each ticket tab, let's only complete the **Expected** section at this stage. Please leave space for an **Actual** section, which we'll fill in later after the agents have run.
>
> In each Expected section, please explain in natural language:
>
> - Which agent the Boss should contact first and why that makes sense for the ticket.
> - The full sequence of agent delegations we expect, including which specialists should work together. Please be specific rather than just saying the Boss contacts everyone.
> - Which MCP tools we expect the agents to use while handling that ticket.
>
> I want the plans to be realistic and based on what each ticket actually needs. We shouldn't involve every specialist in every ticket just for the sake of it, since that would lower our score.
>
> Later, we'll compare these expected workflows with what the agents actually do when resolving all three tickets.
>
> Also, please log this prompt in `AI_prompts.md` as required by Problem 1. Let's only work on Problem 6 for now.

### Follow-Up Prompt

None so far.

## Problem 7 — FastAPI Dashboard Backend

### My Original Prompt

> Now for probelm 7, lets do this: I need u to build the backend that our React dashboard will communicate with in the next problem. Please use FastAPI in `backend/main.py` and set up the following API routes:
>
> - Get all three tickets and show whether each one is still open or already resolved.
> - Accept a ticket ID and start the agent team to work on that specific ticket.
> - Return recent agent activity, including what each agent said and which MCP tools they used, so the dashboard can keep updating.
> - Allow a human to approve a payment or purchase. The agents should only prepare payment requests, and the actual cash deduction must happen through this approval route after someone approves it.
> - Get the current checking account balance from the `cash_accounts` table.
> - Reset the working database back to its original data whenever we want to start a fresh run.
>
> Once the routes are ready, please start the backend from the `backend/` folder using:
>
> `uvicorn main:app --reload --port 8000`
>
> The backend should then be available at `http://localhost:8000`, where the React dashboard can access its API routes.
>
> Also, please update `output/harness.md` with a short description of every route, including its URL and what it does.
>
> Remember to follow our existing shop rules, especially human approval for payments and preventing negative cash balances. Please also log this prompt in `AI_prompts.md`.

### Follow-Up Prompt

> Let's continue with HW5 in this same Codex session. Since port 8000 is currently being used by my HW4 backend, I'd like to use port 8001 temporarily for testing.
>
> For now, please start the HW5 FastAPI backend on port 8001 and run smoke tests to make sure the required Problem 7 API routes work correctly.
>
> Please check ticket retrieval and status, running agents by ticket ID, recent agent events and tool activity, human payment approval, checking account balance, and database reset. For any tests that change data or trigger agent activity, please use disposable database copies and avoid unnecessary AI calls.
>
> A few things I want to be careful about:
>
> - Please don't stop or modify anything related to HW4.
> - Keep the original `data/campus_customs.db` unchanged.
> - Don't reset or modify our actual working database during these tests.
> - Don't run the full three-ticket workflow yet. We'll do that in Problem 9 after resetting the working database and recording the starting cash balance.
> - Make sure the backend will still support port 8000, since that's the port required for the final assignment.
> - For the React dashboard in Problem 8, please make the backend URL easy to configure so we can test on 8001 now and use 8000 later.
> - Don't make any Git commits or push anything to GitHub yet.
>
> Please run the smoke tests, fix any issues related to Problem 7, and tell me which checks passed or failed.
>
> Also, please keep track of any temporary port settings so we can switch everything back to port 8000 before the final submission.
>
> Once everything is verified, stop there and wait for me to provide Problem 8.

**What needed improvement:** The initial backend verification could not bind to port 8000 because HW4 already owned it, and its smoke checks had not yet exercised each dashboard API route or verified reset/payment mutations on disposable copies.

## Problem 8 — React Operations Dashboard

### My Original Prompt

> Now lets work on problem 8: For this part, I need to build a frontend dashboard in `frontend/` using React, Vite, and TypeScript. It should connect to the FastAPI routes we created in Problem 7.
>
> Please make sure the dashboard can:
>
> - Display all three tickets (101, 102, and 103).
> - Let me select a ticket and start the agent team to work on it.
> - Show what each agent is saying and doing while the ticket is being processed.
> - Update the ticket status to resolved once the workflow finishes.
> - Provide a short summary of what each agent contributed to that ticket.
> - Allow a human to approve payments or purchases when approval is requested.
> - Display the current checking account balance and update it after an approved payment reduces the cash available.
>
> I also want the dashboard to look polished and creative. Please put some thought into the layout, visual style, and how the information is presented. It should feel like a real operations dashboard that people would actually enjoy using, not just a basic page with buttons and text.
>
> For the backend connection, the assignment requires `http://localhost:8000`, but since my HW4 server is currently using that port, let's temporarily connect HW5 to `http://localhost:8001` during development. Please make the API URL configurable so we can easily switch back to 8000 for the final version.
>
> Also, please configure the FastAPI backend to allow requests from the Vite frontend, usually running at `http://localhost:5173`, so we don't run into CORS issues.
>
> Once the frontend is ready, start it from `frontend/` using:
>
> `npm run dev`
>
> Please verify that the dashboard opens in the browser and communicates correctly with the backend. For now, avoid running the full three-ticket workflow or making actual changes to our working database, since we'll handle that in Problem 9. Use safe test data or isolated database copies if needed.
>
> Lastly, create `output/design.md` explaining the dashboard design choices, including the layout, how the five agents are visually distinguished, how resolved tickets and cash balances are displayed, and why these creative decisions make the dashboard useful and enjoyable.
>
> Please also record this prompt in `AI_prompts.md` as required by Problem 1.

### Follow-Up Prompt

None so far.

## Problem 9 — Live Ticket Resolution and Cash Reconciliation

### My Original Prompt

> Now problem 9: For this part, we're ready to run the agent team and resolve all three tickets.
>
> Before starting, please reset `data/campus_customs_new.db` to the original database values and record the starting checking balance. Then run tickets 101, 102, and 103 through our React dashboard until all three are resolved.
>
> Please use the actual PydanticAI agents and MCP tools, with all AI calls going through Portkey using only `gpt-6-luna`. If any payment or purchase needs human approval, pause and let me approve it through the dashboard before processing anything. Don't approve payments on my behalf.
>
> After the runs, please update `output/desk_tickets.html`. For each ticket, fill in the **Actual** section with the agents that participated, the tasks they delegated, and the MCP tools they used. Keep the original **Expected** sections so we can compare our plans with what actually happened.
>
> In the **Cash** tab, please include:
>
> - The starting checking balance after the reset.
> - How much cash changed for each ticket, including the reason, payment or purchase involved, and exact dollar amount.
> - The final checking balance, making sure it matches the `cash_accounts` table in our working database.
>
> Please double-check the cash calculations carefully, since incorrect balances can cost points even if all tickets are marked resolved.
>
> Also, create these files:
>
> - `output/resolved_tickets.json`: Include each ticket's ID, final status, a short summary of the outcome, what each agent contributed, and any human approvals.
> - `output/resolved_board.html`: Make a standalone page I can open by double-clicking, with a real screenshot of the React dashboard showing each resolved ticket (101, 102, and 103).
>
> Please append the actual agent runs to `output/audit_trail.json` without deleting earlier records.
>
> Finally, complete `output/harness.md` so it includes the database tables, all MCP tools, five agents, API routes, dashboard, and safety rules. Also, record this prompt in `AI_prompts.md`.
>
> Let's continue using HW5's temporary ports 5174 and 8001 without affecting HW4. Please use real execution results rather than mocked or invented outputs.
>
> If a ticket fails or cannot be resolved, explain what happened instead of marking it resolved without evidence. Don't reset the database again after the real runs without asking me first.

### Follow-Up Prompts

#### Follow-Up 1

> I just approved the $840 invoice payment for Ticket 101 through the dashboard, but the ticket still shows "Open" instead of "Resolved."
> I also noticed an MCP tool error: `ValueError: kind must be 'invoice' or 'rent'`.
> Could you please investigate what happened? Check whether the payment was actually recorded, whether the checking balance changed correctly, and why Ticket 101 is still open.
> Please also review the agent run and MCP tool logs to identify any errors that prevented the ticket from being resolved.
> Don't reset the database, repeat the payment, or rerun the agents yet. First, explain what went wrong and what needs to be fixed. Also, don't change anything related to HW4.

**What needed improvement:** The initial ticket run had failed and the payment approval did not resume it, while the size-S order requirement still needed verification before any resolution could be claimed.

#### Follow-Up 2

> Thanks for checking. Since the $840 payment for Ticket 101 was successfully processed, let's fix the workflow issues before continuing with Problem 9.
>
> Please address the following:
>
> 1. Make sure the agent workflow can safely continue after a human approves a payment, instead of stopping permanently at `needs_human`. It must not create or process duplicate payment requests.
>
> 2. Add a safe, auditable way to update ticket statuses through our existing MCP server. Only mark a ticket as resolved when its actual requirements have been satisfied. Please don't mark Ticket 101 resolved just because invoice #501 was paid.
>
> 3. Fix the Accounting agent's payment tool arguments so it uses the supported values (`invoice` or `rent`) and avoids the earlier validation error.
>
> 4. Review the earlier Portkey request and token-limit errors, and make any necessary improvements without changing the required `gpt-6-luna` model.
>
> 5. Investigate what is still needed to fulfill Ticket 101, especially the missing size S Bulldog tee and whether the database provides enough information to identify a suitable supplier. Please don't invent vendor information or stock availability.
>
> Please preserve the existing $840 payment, invoice status, checking balance, and audit records.
>
> Do not reset the database, make another payment, or mark any ticket resolved without evidence. Keep HW4 completely untouched.
>
> After implementing and testing the fixes safely, explain what changed and whether Ticket 101 can now continue. Please don't automatically start another real agent run yet. Wait for my confirmation.

**What needed improvement:** After diagnosis, the user authorized code fixes and safe disposable-copy tests but required the already-recorded payment and unresolved live ticket state to remain untouched.

#### Follow-Up 3

> Thanks for fixing the workflow. I'd like to continue Ticket 101 now.
> Before I click "Continue agents" in the dashboard, please make sure the backend is running the updated code and that continuing the workflow won't repeat the $840 payment or create duplicate requests.
> Please preserve the current checking balance of $2,560 and all existing audit records.
> If the database doesn't provide enough evidence to restock the exact size-S Bulldog tee, the agents should explain what's missing rather than inventing stock or marking the ticket resolved incorrectly.
> Please confirm when it's safe for me to click "Continue agents." Don't start the run yourself.

**What needed improvement:** The previous response verified the fixes in tests but had not yet confirmed the live backend’s continuation eligibility or current payment and inventory state.

#### Follow-Up 4

> I'm concerned that Ticket 101 may never reach Resolved because the required size-S Bulldog tee has zero stock, while Problem 9 requires all three tickets to be resolved.
> Please carefully review the existing database schema, actual records, and MCP tools to determine the intended way to handle this situation.
> Check whether we can legitimately restock the product, record a purchase or delivery, or resolve the ticket through another valid workflow supported by the available data.
> Please distinguish between what the database actually confirms and what would require assumptions or additional information.
> If our current implementation is too restrictive or missing an intended MCP tool, explain what needs to change and why.
> For now, don't modify the database, create fictional inventory, repeat payments, or force any ticket to Resolved. Just investigate and recommend the safest way to complete Problem 9 correctly.

**What needed improvement:** The prior checks established that Ticket 101 could continue safely, but did not establish a valid purchase-to-delivery path or whether the existing tools could record received stock.

#### Follow-Up 5

> Ticket 101 can never resolve because mark_ticket_resolved requires one CC-TEE-WHITE size S on hand and consumes it, but the system has no way to receive goods and the shop date never advances. Fix the RESOLUTION LOGIC only. Do not change the homework premise.
>
> HARD RULES
> - Do not modify data/campus_customs.db, and do not hand-edit data/campus_customs_new.db (no SQL edits, no inventory bumps, no status changes typed into tables). The working DB may change only through the app's normal tools during a real run, after a reset from the original.
> - Do not add, simulate, or call any "goods received" / inventory-increase action. No shipment can exist on desk.date_today, so recording one would fabricate evidence.
> - Do not add tables or columns to the shop database. Store new decision records outside it (a JSON file managed by the MCP server, plus the audit trail).
> - Keep every existing safety rule: human approval for payments, no negative cash, drafts never sent, all shop data through MCP.
> - Don't run the agent team in this step; I'll do the full run myself afterwards.
>
> CHANGES
> 1. Human decision flow (MCP, in mcp_server/server.py and approval_server.py as fits the current design):
>    - Agent-facing tool request_human_decision(ticket_id, kind, question, recommendation, options). kind is "fulfillment", "discount", or "other". It appends a pending decision (decision_id, created_at in UTC, requested_by) to a file-locked JSON store, refuses a duplicate pending decision of the same kind for the same ticket, and never changes ticket status.
>    - Read-only tool list_decisions(ticket_id=None, status=None).
>    - Human-only tool record_human_decision(decision_id, decided_by, choice, terms). It sets status "decided" with decided_by, choice, terms, and decided_at = current UTC time (never backdated), and refuses unknown or already-decided ids and an empty decided_by. Exclude it from every agent toolset, the same way payment execution is excluded.
>    - A backend route for the human to record the decision from the dashboard (with the approver's name), and a dashboard card with option buttons and a terms field. Log a "human_decision" audit event with all fields.
> 2. Replace the stock-on-hand requirement in mark_ticket_resolved / the resolution check with an explicit final disposition. A customer_order ticket resolves only when:
>    (a) its latest run has completed;
>    (b) every payment or purchase request for that ticket has been decided by a human (approved or rejected);
>    (c) if the order can't be filled from stock (shortfall > 0), a "fulfillment" decision for the ticket has been recorded by a human (for example: defer until restock is confirmed, backorder, or cancel), with terms.
>    Don't require or consume stock when resolving. If anything is missing, keep the ticket open (or "awaiting_decision") and log exactly what's outstanding. Put the rule in one documented function, and log a "final_disposition" audit event when a ticket resolves.
> 3. Prompts: when an order can't be filled, Boss (or Customer Service) must call request_human_decision(kind="fulfillment") with a concrete recommendation (for example: defer the order until a Bulldog Print Co restock is confirmed; customer draft to be reviewed before sending; no delivery date promised). Agents never record decisions themselves.
> 4. Tests (on temporary copies of the DB and decision file): 101 stays unresolved after only the invoice approval; it resolves after the fulfillment decision is recorded; agents can't call record_human_decision; decisions can't be backdated or re-decided.
> 5. Docs: update the harness and README to describe the decision flow and to state clearly that, for an out-of-stock order, "resolved" means the blocking issue was handled and a human explicitly decided the fulfillment outcome. It does NOT mean the item was delivered. In the Reflection, list a human-only receive_purchase_order tool (records the actual quantity received against an approved PO and updates inventory transactionally; owned by Inventory with Accounting reconciling) as something the current team cannot do yet.
> Run the tests and the build, report what changed, and confirm that data/campus_customs.db is unchanged (compare its sha256 before and after).

**What needed improvement:** The existing resolution gate treated physical stock as the only valid completion path, with no audited human disposition, and the dashboard/API lacked the human decision workflow needed to close an unresolved fulfillment blocker honestly.

#### Follow-Up 6

> I'm frustrated with having to fix one issue after another. Please investigate the `UsageLimitExceeded: request_limit of 4` error and fix its root cause.
> I want Problem 9 to run successfully from start to finish without repeatedly encountering new errors.
> Please check the entire agent execution flow for related issues, not just this one error. Run safe automated tests to verify the fixes before telling me it's ready.
> Do not change the homework requirements, original database, HW4, or existing safety rules. Do not run the live agents, reset my working database, or execute payments.
> Once everything is verified, give me simple step-by-step instructions for completing Problem 9 through the dashboard.

**What needed improvement:** The four-request cap was already exhausted by normal agent turns, and failed-call token usage was not retained, so the bounded loop needed more headroom and offline regression coverage for both multi-turn runs and failure handling.

#### Follow-Up 7

> Ticket 101 is still Open after I completed the human fulfillment decision and approved the $840 payment for invoice #501.
>
> The checking balance is now $2,560, pending approvals are 0, but the dashboard shows Agent Runs: 00 and repeated "final disposition blocked" events.
>
> Please perform a READ-ONLY diagnosis of the current state.
>
> Check:
>
> 1. The exact outstanding conditions from the latest final_disposition_blocked audit event.
> 2. Whether invoice #501 is marked Paid and the payment was recorded exactly once.
> 3. Whether my fulfillment decision is saved as Decided.
> 4. Whether the latest workflow failed because of the token limit.
> 5. Why Agent Runs shows 00 despite previous Agent activity.
> 6. Whether there is a safe way to finish the existing workflow without repeating the payment or running the entire Agent team again.
>
> Do not modify any data, run agents, reset anything, approve payments, or change ticket statuses. Do not touch HW4.
>
> Tell me the smallest safe next step to resolve Ticket 101.

**What needed improvement:** The prior execution plan lacked a safe Boss-only recovery path for a token-limited final synthesis, and needed checks proving all completed specialist evidence and current prerequisites before reuse.

#### Follow-Up 8

> We have now identified the exact blocker for Ticket 101: the latest workflow failed because the nested token budget was exceeded before Boss completed final synthesis.
>
> The $840 invoice payment and human fulfillment decision are already recorded. Inventory, Accounting, and Customer Service have completed.
>
> Please implement the smallest safe fix that allows this specific failed workflow to resume from its existing completed specialist evidence, without rerunning those agents.
>
> Requirements:
>
> 1. Preserve the existing $840 payment, $2,560 checking balance, and recorded human fulfillment decision.
> 2. Reuse the completed Inventory, Accounting, and Customer Service results if they are actually persisted and verifiable. Do not fabricate or reconstruct missing evidence.
> 3. Fix the nested token-budget accounting so limits are enforced consistently. Reduce unnecessary context and token consumption before considering reasonable budget adjustments.
> 4. Add a narrowly scoped recovery path for workflows that failed during Boss final synthesis, but only if all required specialist results are saved and no unresolved prerequisites remain.
> 5. Do not automatically mark Ticket 101 resolved. Boss must complete the legitimate workflow, and the existing final-disposition checks must pass.
> 6. Do not repeat the invoice payment, record another fulfillment decision, increase inventory, or claim delivery.
> 7. Test the recovery path offline with mocked LLM responses and temporary data. Do not call paid models during testing.
> 8. Do not modify the original database or HW4, and do not reset the current working database.
> 9. Do not execute the live recovery yet. First report the exact changes, test results, and whether the saved evidence is sufficient for safe recovery.
>
> My goal is to finish Ticket 101 without paying twice or repeating work that has already been completed.

**What needed improvement:** The recovery request needed auditable proof that the saved reports belonged to the failed source workflow, strict live revalidation, shared nested-budget enforcement, and an offline-only test path before enabling a human-triggered recovery.

#### Follow-Up 9

> Ticket 101 is now marked as Resolved, but the actual customer order hasn't been fulfilled because the size-S Bulldog tee is still out of stock.
> Could you please add a small secondary status badge next to the green "Resolved" badge that says "Awaiting Restock", as long as this matches the recorded human fulfillment decision?

**What needed improvement:** The dashboard’s Resolved badge did not separately surface the human-recorded decision to defer fulfillment pending restock, which could be mistaken for delivery.

#### Follow-Up 10

> Now that Ticket 101 is done, I'd like to move on to **Ticket 102 (Rent due)** and continue working on Problem 9.
> Please follow the same Problem 9 requirements we established earlier. Keep all existing records, payments, human decisions, and audit history from Ticket 101.
> Before starting, please check that Ticket 102 is ready to run and that the current workflow can handle it properly. Then let me know when I should start the agents through the dashboard.
> If any payment requires approval, please pause and let me review and approve it myself. Don't automatically approve anything or make human decisions on my behalf.
> Please don't reset the database, rerun Ticket 101, or start Ticket 103 yet. Keep HW4 untouched and continue using HW5's current ports (5174 and 8001).
> Let's focus on Ticket 102 for now, and make sure its actual results and cash changes are properly recorded for Problem 9.

**What needed improvement:** Ticket 101 was complete, but Ticket 102’s lease, timing, cash sufficiency, and human-approval path needed to be verified before a new agent run.

#### Follow-Up 11

> Ticket 102 is done, so let's move directly to **Ticket 103 (Bulk hoodie discount)** and continue Problem 9.
> Please follow the same Problem 9 requirements as before. Keep all existing records, payments, human decisions, and audit history from Tickets 101 and 102.
> Please check that Ticket 103 is ready to run, then let me know when I can click "Start agents" on the dashboard.
> If any human approval or decision is needed, I'll handle it myself. Don't automatically approve anything or make decisions on my behalf.
> Don't reset the database, rerun previous tickets, or start Ticket 103 automatically.
> Let's focus only on Ticket 103 for now. Keep HW4 untouched and continue using ports 5174 and 8001.

**What needed improvement:** Before starting the final ticket, its current stock shortfall, missing discount target, cash/approval state, and supported resolution requirements needed verification.

#### Follow-Up 12

> Ticket 103 failed with `UsageLimitExceeded` because the agent run consumed 13,531 tokens and exceeded the 12,000-token limit.
> Before I retry, could you please investigate and fix this issue?
> Please check why the workflow used so many tokens, including unnecessary agent delegations, repeated MCP calls, or excessive conversation history. Try to make the workflow more efficient while preserving the required agent functionality.
> If the current token limit is genuinely too low, you may increase it to a reasonable value, but please avoid unnecessarily high limits.
> I also noticed that `request_human_decision` succeeded before the failure. Please check whether a pending human decision was already created for Ticket 103 and make sure retrying won't create duplicate requests.
> Please preserve all existing records for Tickets 101 and 102, including payments, human decisions, statuses, and audit history.
> Don't reset the database, delete existing decisions, rerun Ticket 103, or automatically approve anything.
> Keep using only `gpt-6-luna` through Portkey, and don't modify HW4.
> After fixing the issue, run safe tests and let me know whether I should continue the existing Ticket 103 workflow or start a new run. Please wait for my confirmation before triggering any real agents.

**What needed improvement:** Boss exceeded its per-agent synthesis cap despite remaining within the shared workflow budget, and a decided discount request could be recreated on retry while redundant delegation/lookups consumed additional tokens.

#### Follow-Up 13

> Ticket 103 already has its human discount decision, Accounting analysis, and Customer Service draft saved successfully. The only remaining problem seems to be that Boss exceeded the token budget before the workflow could finish and the ticket could be marked Resolved.
>
> I'd like to fix this without repeating the completed work.
>
> Please review the finalization logic and see whether we can safely complete the workflow using the existing saved evidence, without rerunning Accounting, Customer Service, or the full agent team.
>
> If the required business conditions are already satisfied, please implement a safe, auditable finalization or recovery mechanism that can recognize completed specialist work from a failed run.
>
> Make sure this mechanism:
>
> - Uses only real, previously recorded agent outputs and MCP data.
> - Verifies the existing human decision, Accounting analysis, and Customer Service draft.
> - Does not fabricate a successful agent run or erase the original token-limit failure.
> - Does not bypass any required business checks or human approvals.
> - Keeps final ticket status changes behind the MCP server.
> - Prevents duplicate payments, decisions, and board updates.
> - Preserves all existing audit records and records any recovery action separately.
>
> Please also optimize Boss's finalization step to avoid unnecessary token consumption in future runs. Keep the required `gpt-6-luna` model through Portkey.
>
> Run tests using disposable copies of the database and decision records.
>
> Do not modify the live working database, mark Ticket 103 Resolved, or run real agents yet.
>
> After testing, explain whether Ticket 103 can be safely finalized from the existing evidence or whether another agent run is genuinely required. Wait for my confirmation before taking any live action.
>
> Keep Tickets 101 and 102 and the entire HW4 project untouched.

**What needed improvement:** The existing Boss-only recovery covered Ticket 101 only and had no ticket-board read tool to verify Ticket 103's saved analysis and draft against current MCP records.

#### Follow-Up 14

> All three tickets now show Resolved on the HW5 dashboard, including Ticket 103 after the Boss-only recovery.
>
> I'd like to finish Problem 9 now. Please verify the final database statuses, checking balance, payment and purchase records, human approvals and decisions, and actual agent contributions for Tickets 101, 102, and 103.
>
> Then complete the remaining Problem 9 deliverables using only the real recorded results:
>
> - Fill in the Actual sections for all three tickets in `output/desk_tickets.html`, while keeping the original Expected sections unchanged.
> - Complete the Cash tab with the starting balance, cash changes for each ticket, the reasons and amounts, and the final balance matching `cash_accounts`.
> - Create or finalize `output/resolved_tickets.json` with each ticket's ID, final status, outcome, agent contributions, and human approvals.
> - Complete `output/resolved_board.html` with real screenshots of the React dashboard showing each resolved ticket. Please don't use mock screenshots or recreate the dashboard.
> - Preserve the real run history in `output/audit_trail.json`, including failed attempts and recovery actions.
> - Finish `output/harness.md` so it covers database tables, MCP tools, all five agents, API routes, dashboard, and safety rules.
>
> Please verify the cash calculations carefully and clearly distinguish operational resolution from actual order fulfillment when relevant.
>
> Don't reset the database, rerun any agents, process additional payments, or modify HW4. Please let me know if you need me to provide any screenshots manually before finalizing.
>
> After everything is checked, stop and wait for Problem 10.

**What needed improvement:** The Boss recovery completed Ticket 103, but Problem 9 still needed final database-ledger reconciliation, accurate Actual/Cash documentation, and genuine dashboard screenshots for all three tickets.

## Problem 10 — Reflection

### My Original Prompt

> This is problem 10: Problem 10: Reflection (10 Points)
>
> For this part, I need to complete the Reflection tab in `output/desk_tickets.html`. Please use our actual results from Problem 9 and write in a natural, student-like tone. I want the answers to be detailed and thoughtful, but not overly formal or robotic.
>
> Please cover the following:
>
> 1. Evaluate how well the agents performed on each of the three tickets, and explain what worked well and what could have been better.
> 2. Compare the Actual results with the Expected plans we wrote earlier for each ticket. Point out any important differences and why they happened.
> 3. Discuss which parts of our work could have been handled more easily by a single agent with tools, and explain why.
> 4. Come up with three realistic new problems Campus Customs could handle using our current agents and MCP tools.
> 5. Think of three other problems our current system wouldn't be able to solve, and explain what additional tools and agents we'd need to handle them.
>
> Please connect everything to our actual Campus Customs project rather than giving general opinions about AI. Use the three ticket tabs, Cash tab, agent activity, and audit records as evidence.
>
> For example, we can discuss Ticket 101's inventory shortage and human fulfillment decision, Ticket 102's rent payment, and Ticket 103's discount request and recovery process. Please also refer to the actual cash changes where relevant.
>
> Keep the writing natural and personal, with clear reasoning and specific examples. I'd prefer straightforward language over complicated technical explanations, but please don't leave out important details.
>
> Also, record this prompt in `AI_prompts.md` as required by Problem 1. Only work on Problem 10 for now, and don't change any existing ticket results, payments, or database records.

### Follow-Up Prompt

None so far.

## Problem 11 — Final GitHub Submission

### My Original Prompt

> ## Problem 11: Final GitHub Submission (5 Points)
>
> We're almost done with HW5! For this last part, I need to upload our completed project to a **public GitHub repository** so the graders can clone it and run everything.
>
> Please make sure the repository URL is saved in `output/github_url.txt`. I'll also need to submit that same link on Canvas.
>
> Before uploading, let's carefully check that all the required files are included and nothing important is missing.
>
> The project should have the following structure:
> ```
> hw5/
> ├── AI_prompts.md
> ├── requirements.txt
> ├── .env.example
> ├── .gitignore
> ├── .mcp.json
> ├── README.md
> ├── data/
> │   ├── campus_customs.db
> │   └── campus_customs_new.db
> ├── mcp_server/
> │   ├── server.py
> │   └── README.md
> ├── frontend/
> ├── backend/
> │   ├── main.py
> │   ├── models.py
> │   └── prompts/
> │       ├── boss.md
> │       ├── inventory.md
> │       ├── accounting.md
> │       ├── facilities.md
> │       └── customer_service.md
> └── output/
>     ├── harness.md
>     ├── mcp_smoke.json
>     ├── desk_tickets.html
>     ├── design.md
>     ├── resolved_tickets.json
>     ├── resolved_board.html
>     ├── audit_trail.json
>     └── github_url.txt
> ```
>
> Please include both the original database and our updated working database in `data/`, since the graders need both to run the project and review our results.
>
> Make sure the real `.env` file, API keys, passwords, and other sensitive information are not uploaded. We should include `.env.example` with placeholder values instead.
>
> Also, please check that any images or screenshots referenced by `output/resolved_board.html` are included so the page works when someone clones the repository.
>
> The main `README.md` should clearly explain how to set up the project, restore the working database from the original for a fresh run, start the MCP server, launch the FastAPI backend, and run the React dashboard. It should also explain that the database needs to be reset before running all three tickets again.
>
> Before pushing, please verify that:
>
> - All required files are present and the project can be built and run.
> - The backend and frontend use the required ports, 8000 and 5173.
> - All five agents use only `gpt-6-luna` through Portkey.
> - The three resolved tickets, payment records, human decisions, screenshots, and Reflection are preserved.
> - The repository contains no real secrets or unnecessary files such as virtual environments, `node_modules`, or caches.
> - HW4 and its GitHub repository remain completely untouched.
>
> Once everything has been checked, please push HW5 to its own public GitHub repository and save the final URL in `output/github_url.txt`.
>
> After pushing, confirm that the repository is public, the required files are visible on GitHub, and the uploaded version matches our local project.
>
> Please don't reset the database, rerun any agents, or change the completed ticket results during this process.
>
> Finally, give me the GitHub URL so I can submit it on Canvas.

### Follow-Up Prompt

None so far.
