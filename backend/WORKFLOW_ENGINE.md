# Workflow Engine

## Purpose

A workflow is a named, versioned graph of nodes that Auralink executes with
the same governance as an agent run: leases, fencing, deterministic state,
limits enforced outside the model, escalation for ambiguity.

The unit of work changes from "an agent with instructions" to "a business
process with branches." The discipline does not.

## MVP scope (Stage 3.1)

Five node types ship. Everything else is deferred.

| Node        | Purpose |
|-------------|---------|
| 	rigger   | Entry point. Marks input received, transitions to next. |
| gent     | Runs a Stage 2 AgentRun as a sub-run. Reuses existing engine. |
| 	ool      | Calls a tool directly, records result. No agent, no LLM. |
| condition | Evaluates a JSONLogic expression against run_context. Routes to one branch. |
| 	erminate | Ends the run with a terminal reason. |

Deferred to 3.2+: parallel forks, delays, retries-per-node, sub-workflow
calls, webhook/schedule/event triggers, loops.

Deferred to Stage 5: approval nodes, human-in-the-loop, non-manual triggers.

## Data model

Reuse existing run/step tables. A workflow run IS an AgentRun row.
A workflow step IS an execution_steps row.

New tables:

    workflows
      id              VARCHAR(36) PRIMARY KEY
      org_id          VARCHAR(36) NOT NULL
      name            VARCHAR(200) NOT NULL
      description     TEXT
      status          VARCHAR(32) NOT NULL DEFAULT 'draft'
                      -- draft | active | archived
      current_version_id VARCHAR(36) NULL
      created_by      VARCHAR(36) NULL
      created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
      updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
      UNIQUE(org_id, name)

    workflow_versions
      id              VARCHAR(36) PRIMARY KEY
      workflow_id     VARCHAR(36) NOT NULL REFERENCES workflows(id) ON DELETE CASCADE
      org_id          VARCHAR(36) NOT NULL
      version         INTEGER NOT NULL
      graph           JSONB NOT NULL
      created_by      VARCHAR(36) NULL
      created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
      UNIQUE(workflow_id, version)

    Both tables get ENABLE + FORCE ROW LEVEL SECURITY, policy on
    current_setting('app.org_id'), matching every other tenant table.

Extended columns (nullable, no backfill needed):

    agent_runs
      workflow_version_id VARCHAR(36) NULL
      -- NULL = plain agent run, non-NULL = workflow run

    execution_steps
      node_id VARCHAR(64) NULL
      -- NULL = plain step, non-NULL = which workflow node produced it

## Graph format

    {
      "entry_node_id": "trig",
      "nodes": [
        {"id": "trig", "type": "trigger", "next": "extract"},
        {"id": "extract", "type": "agent", "next": "check",
         "config": {"agent_id": "...", "instructions_override": null}},
        {"id": "check", "type": "condition", "branches": [
          {"when": {"!=": [{"var": "last_agent_output"}, null]}, "next": "record"},
          {"when": true, "next": "end_fail"}
        ]},
        {"id": "record", "type": "tool", "next": "end_ok",
         "config": {"tool_key": "record_order", "arguments_template": {...}}},
        {"id": "end_ok", "type": "terminate",
         "config": {"reason": "completed"}},
        {"id": "end_fail", "type": "terminate",
         "config": {"reason": "failed", "detail": "no agent output"}}
      ],
      "edges": [
        ["trig", "extract"], ["extract", "check"],
        ["check", "record"], ["check", "end_fail"],
        ["record", "end_ok"]
      ]
    }

Validation at save time:

    1. Exactly one node with id == entry_node_id, type == "trigger".
    2. All node ids unique.
    3. Every edge's source and target exist.
    4. Every non-terminate node has at least one outgoing edge.
    5. Terminate nodes have no outgoing edges.
    6. Condition nodes have 2+ branches, all with valid 
ext.
    7. Graph is acyclic (topological sort succeeds).
    8. All referenced tool_keys and agent_ids are visible to the org.

Node kind by type:

| type        | required fields                                  |
|-------------|--------------------------------------------------|
| trigger     | next                                             |
| agent       | next, config.agent_id                            |
| tool        | next, config.tool_key                            |
| condition   | branches (2+), each {when, next}                 |
| terminate   | config.reason                                    |

## Execution model

WorkflowEngine.run_once(workflow_run_id, org_id):

    1. claim_lease (same mechanism as RuntimeEngine)
    2. load WorkflowRunState:
         - workflow_version_id and graph
         - current node id (from run_context)
         - prior step history (execution_steps filtered by node_id IS NOT NULL)
         - counters (aggregated)
    3. loop:
         a. decision = decide_next_node(state)
         b. if terminate: write_terminated, release_lease, return
         c. dispatch node handler:
              - trigger:        mark input, transition to next, persist
              - agent:          create AgentRun sub-run; await its terminal
                                state; record success/failure on this step;
                                transition per config
              - tool:           execute tool via registry; record result;
                                transition to next
              - condition:      evaluate JSONLogic over run_context;
                                select branch; transition to matching next
         d. persist execution_step with node_id set
         e. update run_context.current_node_id
         f. enforce limits (workflow-wide, aggregated across nodes)
    4. every step fenced with heartbeat check

decide_next_node(state) is pure: state in, NodeDecision out.

    NodeDecision:
      kind: Literal["run_node", "terminate"]
      node_id: str | None
      terminal_reason: str | None

Same guarantees as Stage 2:

    - bounded iterations (max_nodes_executed)
    - bounded tool calls
    - bounded wall time
    - bounded cost
    - deterministic terminal states
    - crash recovery via lease + fencing
    - idempotency via idempotency_key on all side-effecting calls

## Agent node — sub-run semantics

When a workflow reaches an agent node:

    1. WorkflowEngine creates an AgentRun row with:
         workflow_version_id = <this workflow version>
         agent_version_id   = config.agent_id's current version
         status             = 'pending'
         input              = workflow's run_context at this point
    2. WorkflowEngine calls RuntimeEngine.run_once on the sub-run
    3. When the sub-run reaches terminal state:
         - 'completed': workflow records success, transitions to config.next
         - 'failed'/'escalated'/'timed_out'/'budget_exceeded'/'cancelled':
             workflow terminates with matching reason
    4. Sub-run's full execution_steps persist independently, node_id IS NULL
       (sub-run is a plain agent run)

Why sub-run: separation of concerns. Limits are per-run, not pooled.
A workflow can have 5 agent nodes; each gets its own budget. Cost is
attributed to each node. Failures in one node don't corrupt another's
state. And a future "run inspector" UI shows "workflow X, node Y, sub-run Z"
naturally.

Trade-off: a workflow run's cost is the sum of its sub-runs, plus
overhead. For monitoring, we aggregate. For limits, we sum.

## Limits

Workflow-wide limits (enforced in decide_next_node, aggregated across nodes):

    max_nodes_executed: int = 100
    max_tool_calls:     int = 50
    max_seconds:        int = 300
    max_cost_usd:       Decimal = 5.00

Per-node limits (in each node's config, all optional):

    agent node:  max_seconds, max_cost_usd (passed to sub-run)
    tool node:   max_seconds (passed to registry executor)

Depth limit (Stage 3.2+ when sub-workflows exist):

    max_depth: int = 3

## Expression language — JSONLogic

Condition nodes evaluate JSONLogic expressions against a context object:

    {
      "run_input":         <the workflow's input>
      "last_agent_output": <output of most recent agent node, or null>
      "last_tool_output":  <output of most recent tool node, or null>
      "nodes":             {"<node_id>": <that node's last output>}
      "counters":          {"agent_calls": int, "tool_calls": int}
    }

JSONLogic is:
    - pure JSON (no code, no eval)
    - safe against injection (no server-side execution)
    - well-specified (jsonlogic.com)
    - supported by a small pip package (json-logic-qubit or equivalent)

We do NOT evaluate JS or Python expressions. JSONLogic only.

## API surface (Stage 3.1)

    POST   /api/v1/workflows                create (draft)
    GET    /api/v1/workflows                list (org-scoped)
    GET    /api/v1/workflows/{id}           get
    POST   /api/v1/workflows/{id}/versions  create version (validate graph)
    POST   /api/v1/workflows/{id}/deploy    flip current_version_id
    POST   /api/v1/workflows/{id}/runs      start a run (manual trigger)
    GET    /api/v1/runs/{id}                get run status + step trace

All routes use require(action, resource) from authorization matrix.
New resources/actions: WORKFLOW (CRUD), RUN (extended for workflows).

## Milestones

### 3.1a — Schema (2-3 hours)

- Migration 0007: workflows, workflow_versions tables, RLS, grants
- ALTER agent_runs ADD workflow_version_id
- ALTER execution_steps ADD node_id
- Extend ORM models: Workflow, WorkflowVersion
- Extend execution_steps write path to accept node_id
- Extend agent_runs insert path to accept workflow_version_id
- Add schema drift tests for new tables
- Add tenant isolation tests (2 new parametrized cases)

Exit gate: migrations apply clean, 97 + 6 tests pass.

### 3.1b — Graph model + validation (4-6 hours)

- New module: app/modules/workflows/
  - models.py (ORM)
  - schemas.py (Pydantic: WorkflowGraph, Node, Edge, NodeType enum)
  - graph.py (validation: unique ids, edges valid, DAG, node type schemas)
  - service.py (create_workflow, create_version, deploy_version, list_workflows)
- Pydantic strict validation of the graph JSONB shape
- Tests: 8-10 graph validation cases (invalid node types, missing edges, cycles, dangling refs, tool/agent visibility)

Exit gate: graph validation tests pass, WorkflowGraph model round-trips.

### 3.1c — WorkflowEngine (linear + terminate only) (6-8 hours)

- New file: app/modules/workflows/engine.py
  - WorkflowEngine class mirroring RuntimeEngine
  - decide_next_node pure function in app/modules/workflows/decision.py
  - Node handlers for: trigger, agent (sub-run), terminate
  - Lease, fencing, limits — reuse Stage 2 patterns
- 4-6 integration tests: trigger?terminate, trigger?agent?terminate, agent sub-run success/failure propagation, limits enforced

Exit gate: linear workflows execute end-to-end with sub-runs, one smoke script.

### 3.1d — Condition nodes (4-6 hours)

- Add json-logic-qubit to requirements
- Evaluate JSONLogic in condition node handler
- 8 tests: expression evaluation, branch selection, malformed expression handling, missing vars, nested conditions

Exit gate: condition branches correctly route based on run_context.

### 3.1e — Tool nodes (2-3 hours)

- Direct tool invocation without agent
- Idempotency, timeout, error handling reuse registry primitives
- 4 tests: successful tool call, tool not granted, tool timeout, tool failure escalates

Exit gate: tool nodes work standalone, contribute to workflow run_context.

### 3.1f — End-to-end smoke (2 hours)

- smoke_workflow_e2e.py mirroring smoke_e2e.py
- The full flow: trigger ? agent ? condition ? tool ? terminate
- 1 PASS result persisted, verifiable

Exit gate: workflow runs end-to-end with all 5 node types.

### 3.1g — API routes (4-6 hours)

- All routes above with require(action, resource)
- Authorization matrix extended for WORKFLOW
- 4-6 tests: create/get/list workflows, deploy, run, get run status

Exit gate: API calls work through FastAPI, authz enforced, 3.1 ships.

## Total Stage 3.1 estimate

    3.1a: 2-3h
    3.1b: 4-6h
    3.1c: 6-8h
    3.1d: 4-6h
    3.1e: 2-3h
    3.1f: 2h
    3.1g: 4-6h
    ------------------
    Total: ~24-34 hours focused implementation

Plus review, debugging, integration fixes: realistically 3-5 days.

## What Stage 3.1 does not solve (and shouldn't try)

- Parallelism — 3.2
- Retry policies per node — 3.2
- Non-manual triggers — Stage 5
- Approvals / human-in-the-loop — Stage 5
- Visual editor — Stage 3.2 frontend
- Workflow templates — Stage 6
- Sub-workflow calls — 3.2

## ADRs this spec depends on

- ADR-0001 (typed engine state) — workflow step payloads follow the same pattern
- ADR-0003 (step contract tests) — apply to workflow step pairs
- ADR-0004 (redaction per table) — workflow step inputs follow execution_steps policy
- ADR-0005 (identifier discipline) — workflow/version ids use uuid7str(), test fixtures use uuid4

## Open questions deferred

- Should agent node's output be the sub-run's final output field, or the sub-run's full terminal step? -- For MVP: sub-run's terminal step output. Full trace accessible via sub-run_id.
- How to surface a workflow's step trace vs sub-run traces in the API? -- For 3.1g: two separate endpoints. /runs/{id} returns workflow steps; /runs/{id}/sub-runs returns list; each sub-run has its own /runs/{sub_id} for full trace.
- What happens when a workflow reaches a node with no matching outgoing edge (condition with no branch matches)? -- For MVP: escalate with reason "no_branch_matched". This is a validation failure at runtime, not save time.
