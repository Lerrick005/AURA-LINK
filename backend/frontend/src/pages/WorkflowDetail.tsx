import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiGet, apiPost, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface Workflow {
  id: string;
  name: string;
  description: string | null;
  status: string;
  current_version_id: string | null;
  created_at: string;
  updated_at: string;
}

interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version: number;
  created_at: string;
}

interface RunSummary {
  id: string;
  workflow_id: string | null;
  workflow_name: string | null;
  status: string;
  terminal_reason: string | null;
  cost_usd: string;
  tokens_in: number;
  tokens_out: number;
  started_at: string;
  finished_at: string | null;
}

interface RunList {
  items: RunSummary[];
  total: number;
  limit: number;
  offset: number;
}

const DEFAULT_GRAPH = {
  entry_node_id: "start",
  nodes: [
    { id: "start", type: "trigger", next: "end", config: null, branches: null },
    {
      id: "end",
      type: "terminate",
      next: null,
      config: { reason: "completed" },
      branches: null,
    },
  ],
  edges: [],
};

const ACTIVE = new Set(["pending", "running"]);

export default function WorkflowDetail() {
  const { auth } = useAuth();
  const { id } = useParams<{ id: string }>();
  const [wf, setWf] = useState<Workflow | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [pendingVersionId, setPendingVersionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function loadWf() {
    if (!auth || !id) return;
    try {
      const row = await apiGet<Workflow>(`/workflows/${id}`, auth.token);
      setWf(row);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Load failed");
    }
  }

  async function loadRuns() {
    if (!auth || !id) return;
    try {
      const list = await apiGet<RunList>(
        `/runs?workflow_id=${id}&limit=20`,
        auth.token,
      );
      setRuns(list.items);
    } catch {
      // runs list stays empty; don't spam
    }
  }

  useEffect(() => {
    void loadWf();
    void loadRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth?.token, id]);

  const hasActiveRun = runs.some((r) => ACTIVE.has(r.status));
  useEffect(() => {
    if (!hasActiveRun) return;
    const t = setInterval(() => void loadRuns(), 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActiveRun]);

  async function onCreateVersion() {
    if (!auth || !id) return;
    setBusy("version");
    setError(null);
    try {
      const ver = await apiPost<WorkflowVersion>(
        `/workflows/${id}/versions`,
        auth.token,
        { graph: DEFAULT_GRAPH },
      );
      // Cache the version id so Deploy can use it immediately.
      setPendingVersionId(ver.id);
      await loadWf();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Create version failed");
    } finally {
      setBusy(null);
    }
  }

  async function onDeploy() {
    if (!auth || !id) return;
    // Prefer the version we just created; fall back to the workflow's
    // current_version_id (re-deploy of same version is a no-op).
    const versionId = pendingVersionId ?? wf?.current_version_id ?? null;
    if (!versionId) {
      setError("Create a version first.");
      return;
    }
    setBusy("deploy");
    setError(null);
    try {
      // Deploy uses a query param on the route (see backend routes.py)
      await apiPost(
        `/workflows/${id}/deploy?version_id=${versionId}`,
        auth.token,
        {},
      );
      setPendingVersionId(null);
      await loadWf();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Deploy failed");
    } finally {
      setBusy(null);
    }
  }

  async function onTrigger() {
    if (!auth || !id) return;
    setBusy("trigger");
    setError(null);
    try {
      await apiPost(
        `/workflows/${id}/runs`,
        auth.token,
        { input_data: { task: "manual trigger from UI" } },
      );
      // Brief delay before first poll so the run row exists
      await new Promise((r) => setTimeout(r, 300));
      await loadRuns();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Trigger failed");
    } finally {
      setBusy(null);
    }
  }

  if (error && !wf) {
    return (
      <div className="text-sm text-red-400 bg-red-950/40 border border-red-900 rounded px-3 py-2">
        {error}
      </div>
    );
  }
  if (!wf) return <div className="text-sm text-gray-400">Loading...</div>;

  const deployed = wf.status === "active" && wf.current_version_id !== null;
  const canDeploy = pendingVersionId !== null && !deployed;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/workflows" className="text-sm text-gray-400 hover:text-gray-200">
          ← Back to workflows
        </Link>
        <div className="mt-2 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">{wf.name}</h1>
            {wf.description && (
              <p className="text-sm text-gray-400 mt-1">{wf.description}</p>
            )}
          </div>
          <span
            className={
              "text-xs uppercase tracking-wide px-2 py-1 rounded shrink-0 " +
              (deployed
                ? "bg-emerald-900/40 text-emerald-300"
                : "bg-gray-800 text-gray-400")
            }
          >
            {deployed ? "deployed" : wf.status}
          </span>
        </div>
      </div>

      {error && (
        <div className="text-sm text-red-400 bg-red-950/40 border border-red-900 rounded px-3 py-2">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-2 items-center">
        <button
          onClick={onCreateVersion}
          disabled={busy !== null}
          className="bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-gray-200 text-sm rounded px-3 py-1.5"
        >
          {busy === "version" ? "Creating..." : "Create default version"}
        </button>
        <button
          onClick={onDeploy}
          disabled={busy !== null || (!canDeploy && !deployed)}
          className="bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-gray-200 text-sm rounded px-3 py-1.5"
        >
          {busy === "deploy"
            ? "Deploying..."
            : deployed
            ? "Re-deploy"
            : "Deploy"}
        </button>
        <button
          onClick={onTrigger}
          disabled={busy !== null || !deployed}
          className="bg-accent-500 hover:bg-accent-400 disabled:opacity-50 text-white text-sm font-medium rounded px-3 py-1.5"
        >
          {busy === "trigger" ? "Triggering..." : "Trigger run"}
        </button>
        {pendingVersionId && !deployed && (
          <span className="text-xs text-amber-400">
            Version created — click Deploy to activate
          </span>
        )}
      </div>

      <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Current version</dt>
          <dd className="mt-1 font-mono text-xs truncate">{wf.current_version_id ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Created</dt>
          <dd className="mt-1">{new Date(wf.created_at).toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Updated</dt>
          <dd className="mt-1">{new Date(wf.updated_at).toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Runs</dt>
          <dd className="mt-1">{runs.length}</dd>
        </div>
      </dl>

      <div>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
          Recent runs
        </h2>
        {runs.length === 0 ? (
          <div className="border border-dashed border-surface-600 rounded-lg p-8 text-sm text-gray-400 text-center">
            No runs yet. {deployed ? "Click Trigger run above." : "Deploy a version first."}
          </div>
        ) : (
          <ul className="divide-y divide-surface-700 border border-surface-700 rounded-lg overflow-hidden">
            {runs.map((r) => (
              <li key={r.id} className="bg-surface-800 hover:bg-surface-700/60">
                <Link to={`/runs/${r.id}`} className="block px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <RunStatusBadge status={r.status} />
                      <span className="font-mono text-xs text-gray-500 truncate max-w-xs">
                        {r.id}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-gray-400">
                      <span>${r.cost_usd}</span>
                      <span>{r.tokens_in + r.tokens_out} tok</span>
                      <span>{new Date(r.started_at).toLocaleTimeString()}</span>
                    </div>
                  </div>
                  {r.terminal_reason && (
                    <div className="text-xs text-gray-500 mt-1">
                      terminal: {r.terminal_reason}
                    </div>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RunStatusBadge({ status }: { status: string }) {
  const active = ACTIVE.has(status);
  const good = status === "completed";
  const bad = status === "failed" || status === "escalated" || status === "timed_out";
  const cls = good
    ? "bg-emerald-900/40 text-emerald-300"
    : bad
    ? "bg-red-900/40 text-red-300"
    : active
    ? "bg-blue-900/40 text-blue-300 animate-pulse"
    : "bg-gray-800 text-gray-400";
  return (
    <span className={"text-xs uppercase tracking-wide px-2 py-0.5 rounded " + cls}>
      {status}
    </span>
  );
}