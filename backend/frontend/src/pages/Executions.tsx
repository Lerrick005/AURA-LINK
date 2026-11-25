import { useEffect, useState } from "react";
import { apiGet, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { FaSpinner, FaCheckCircle, FaTimesCircle, FaClock, FaBolt } from "react-icons/fa";

interface Run {
  id: string;
  workflow_id: string | null;
  workflow_name: string | null;
  workflow_version_id: string | null;
  status: string;
  terminal_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: string | number | null;
}

export default function Executions() {
  const { auth } = useAuth();
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadRuns = () => {
    if (!auth) return;
    setLoading(true);
    setError(null);
    apiGet<any>("/runs?limit=100", auth.token)
      .then((data) => {
        const items: Run[] = Array.isArray(data) ? data : (data.items || []);
        setRuns(items.filter((r) => r.workflow_version_id));
      })
      .catch((err) => {
        setError(err instanceof ApiError
          ? `Backend error (${err.status}): ${err.message}`
          : "Failed to load executions.");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadRuns(); }, [auth]);

  return (
    <div className="max-w-7xl mx-auto flex flex-col h-full">
      <div className="mb-6 flex justify-between items-end shrink-0">
        <div>
          <h1 className="text-3xl font-serif text-gray-900 tracking-tight">Executions</h1>
          <p className="text-gray-500 text-sm mt-1">Every workflow run, with outcomes.</p>
        </div>
        <button onClick={loadRuns} className="text-xs text-[#0a5c36] hover:underline font-medium">
          Refresh
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-xl mb-6 text-sm flex items-center gap-2">
          <FaTimesCircle /> {error}
        </div>
      )}

      <div className="flex-1 bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden flex flex-col">
        <div className="p-4 border-b border-gray-100 bg-gray-50 shrink-0">
          <h2 className="text-sm font-semibold text-gray-700">Runs ({runs.length})</h2>
        </div>
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-400">
              <FaSpinner className="animate-spin mr-2" /> Loading executions...
            </div>
          ) : runs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-400 p-8 text-center">
              <div className="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center text-2xl text-gray-300 mb-4">
                <FaBolt />
              </div>
              <p className="text-lg font-medium text-gray-600 mb-1">No executions yet</p>
              <p className="text-sm">Run a workflow from the Workflows page and its execution will appear here.</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {runs.map((run) => (
                <div key={run.id} className="p-4 flex items-center justify-between hover:bg-gray-50">
                  <div className="flex items-center gap-3">
                    <RunStatusIcon status={run.status} />
                    <div>
                      <div className="text-sm font-medium text-gray-900">
                        {run.workflow_name || "—"}
                      </div>
                      <div className="text-xs text-gray-500 font-mono">
                        {run.id.slice(0, 8)}… {run.started_at ? new Date(run.started_at).toLocaleString() : ""}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-6 text-xs">
                    <div className="text-gray-500">{(run.tokens_in || 0) + (run.tokens_out || 0)} tokens</div>
                    <div className="text-gray-500">${Number(run.cost_usd || 0).toFixed(4)}</div>
                    <RunStatusBadge status={run.status} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RunStatusIcon({ status }: { status: string }) {
  if (status === "completed") return <FaCheckCircle className="text-emerald-500" />;
  if (status === "failed" || status === "timed_out") return <FaTimesCircle className="text-red-500" />;
  if (status === "running" || status === "pending") return <FaSpinner className="text-blue-500 animate-spin" />;
  return <FaClock className="text-gray-400" />;
}

function RunStatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: "text-emerald-700 bg-emerald-50 border-emerald-200",
    failed: "text-red-700 bg-red-50 border-red-200",
    escalated: "text-orange-700 bg-orange-50 border-orange-200",
    running: "text-blue-700 bg-blue-50 border-blue-200",
    pending: "text-gray-600 bg-gray-100 border-gray-200",
    timed_out: "text-red-700 bg-red-50 border-red-200",
    cancelled: "text-gray-600 bg-gray-100 border-gray-200",
    budget_exceeded: "text-orange-700 bg-orange-50 border-orange-200",
  };
  return (
    <span className={`px-2.5 py-1 rounded-md text-xs font-semibold border ${colors[status] || colors.pending}`}>
      {status}
    </span>
  );
}