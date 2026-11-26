import { useEffect, useState } from "react";
import { apiGet, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useNavigate } from "react-router-dom";

interface Run {
  id: string;
  workflow_name: string | null;
  status: string;
  started_at: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: string | number | null;
}

export default function Executions() {
  const { auth } = useAuth();
  const navigate = useNavigate();
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
        setRuns(items);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          localStorage.removeItem("auth");
          navigate("/login");
          return;
        }
        setError(err instanceof ApiError
          ? `Backend error (${err.status}): ${err.message}`
          : "Failed to load executions.");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth?.token]);

  const statusBadge = (status: string) => {
    const s = status.toLowerCase();
    const color =
      s === "completed" ? "bg-green-100 text-green-800" :
      s === "failed" ? "bg-red-100 text-red-800" :
      s === "escalated" ? "bg-amber-100 text-amber-800" :
      s === "running" ? "bg-blue-100 text-blue-800" :
      "bg-gray-100 text-gray-800";
    return <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${color}`}>{s}</span>;
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Executions</h1>
        <p className="text-sm text-gray-600 mt-1">Every workflow run, with outcomes.</p>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 text-sm rounded-md border border-red-200">
          {error}
        </div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-gray-200">
        <div className="flex justify-between items-center px-4 py-3 border-b border-gray-200">
          <h2 className="font-medium text-gray-900">Runs ({runs.length})</h2>
          <button onClick={loadRuns} className="text-sm text-green-700 hover:text-green-900">Refresh</button>
        </div>
        {loading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : runs.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No executions yet. Try running a workflow from the Workflows page.
          </div>
        ) : (
          <ul className="divide-y divide-gray-200">
            {runs.map((run) => (
              <li key={run.id} className="px-4 py-3 hover:bg-gray-50">
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-gray-900 truncate">
                      {run.workflow_name || "Workflow Run"}
                    </div>
                    <div className="text-xs text-gray-500 mt-1 font-mono">
                      {run.id.slice(0, 18)}...
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {statusBadge(run.status)}
                    <div className="text-xs text-gray-500 text-right">
                      <div>{run.tokens_in ?? 0} + {run.tokens_out ?? 0} tokens</div>
                      <div>${Number(run.cost_usd ?? 0).toFixed(6)}</div>
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}