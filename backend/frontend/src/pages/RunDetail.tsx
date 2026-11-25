import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiGet, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface Step {
  seq: number;
  type: string;
  status: string;
  node_id: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: string | null;
  error: Record<string, unknown> | null;
  started_at: string;
  finished_at: string | null;
}

interface Run {
  id: string;
  workflow_id: string | null;
  workflow_name: string | null;
  workflow_version_id: string | null;
  status: string;
  terminal_reason: string | null;
  cost_usd: string;
  tokens_in: number;
  tokens_out: number;
  started_at: string;
  finished_at: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  steps: Step[];
}

const ACTIVE = new Set(["pending", "running"]);

export default function RunDetail() {
  const { auth } = useAuth();
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!auth || !runId) return;
    try {
      const row = await apiGet<Run>(`/runs/${runId}`, auth.token);
      setRun(row);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Load failed");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth?.token, runId]);

  const active = run && ACTIVE.has(run.status);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => void load(), 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  if (error) {
    return (
      <div className="text-sm text-red-400 bg-red-950/40 border border-red-900 rounded px-3 py-2">
        {error}
      </div>
    );
  }
  if (!run) return <div className="text-sm text-gray-400">Loading...</div>;

  const backHref = run.workflow_id ? `/workflows/${run.workflow_id}` : "/workflows";

  return (
    <div className="space-y-6">
      <div>
        <Link to={backHref} className="text-sm text-gray-400 hover:text-gray-200">
          ← Back to workflow
        </Link>
        <div className="mt-2 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold flex items-center gap-3">
              Run
              <span className="font-mono text-sm text-gray-500">{run.id}</span>
            </h1>
            {run.workflow_name && (
              <p className="text-sm text-gray-400 mt-1">
                Workflow: {run.workflow_name}
              </p>
            )}
          </div>
          <span
            className={
              "text-xs uppercase tracking-wide px-2 py-1 rounded shrink-0 " +
              (run.status === "completed"
                ? "bg-emerald-900/40 text-emerald-300"
                : run.status === "failed" || run.status === "escalated"
                ? "bg-red-900/40 text-red-300"
                : "bg-blue-900/40 text-blue-300")
            }
          >
            {run.status}
          </span>
        </div>
      </div>

      <dl className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Started</dt>
          <dd className="mt-1">{new Date(run.started_at).toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Finished</dt>
          <dd className="mt-1">
            {run.finished_at ? new Date(run.finished_at).toLocaleString() : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Cost</dt>
          <dd className="mt-1">${run.cost_usd}</dd>
        </div>
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Tokens</dt>
          <dd className="mt-1">
            {run.tokens_in} in / {run.tokens_out} out
          </dd>
        </div>
        <div>
          <dt className="text-gray-500 text-xs uppercase tracking-wide">Terminal</dt>
          <dd className="mt-1">{run.terminal_reason ?? "—"}</dd>
        </div>
      </dl>

      <div className="grid md:grid-cols-2 gap-4">
        <JsonBlock label="Input" value={run.input} />
        <JsonBlock label="Output" value={run.output} />
      </div>

      <div>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
          Steps ({run.steps.length})
        </h2>
        <ol className="space-y-2">
          {run.steps.map((s) => (
            <li
              key={s.seq}
              className="bg-surface-800 border border-surface-700 rounded-lg px-4 py-3"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-gray-500">#{s.seq}</span>
                  <span className="font-medium text-sm">{s.type}</span>
                  {s.node_id && (
                    <span className="text-xs text-gray-500">
                      node: <span className="font-mono">{s.node_id}</span>
                    </span>
                  )}
                </div>
                <span
                  className={
                    "text-xs uppercase tracking-wide px-2 py-0.5 rounded " +
                    (s.status === "ok"
                      ? "bg-emerald-900/40 text-emerald-300"
                      : "bg-red-900/40 text-red-300")
                  }
                >
                  {s.status}
                </span>
              </div>
              <div className="text-xs text-gray-500 mt-2 flex gap-4">
                <span>{new Date(s.started_at).toLocaleTimeString()}</span>
                {s.finished_at && (
                  <span>
                    took{" "}
                    {(
                      (new Date(s.finished_at).getTime() -
                        new Date(s.started_at).getTime()) /
                      1000
                    ).toFixed(2)}
                    s
                  </span>
                )}
                {s.cost_usd && <span>${s.cost_usd}</span>}
              </div>
              {(s.input || s.output) && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-gray-400 hover:text-gray-200">
                    Show input / output
                  </summary>
                  <div className="mt-2 grid md:grid-cols-2 gap-2">
                    {s.input && <JsonBlock label="in" value={s.input} compact />}
                    {s.output && <JsonBlock label="out" value={s.output} compact />}
                  </div>
                </details>
              )}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function JsonBlock({
  label,
  value,
  compact,
}: {
  label: string;
  value: Record<string, unknown> | null | undefined;
  compact?: boolean;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">{label}</div>
      <pre
        className={
          "bg-surface-900 border border-surface-700 rounded p-3 text-xs overflow-auto text-gray-300 " +
          (compact ? "max-h-48" : "max-h-72")
        }
      >
        {value ? JSON.stringify(value, null, 2) : "—"}
      </pre>
    </div>
  );
}