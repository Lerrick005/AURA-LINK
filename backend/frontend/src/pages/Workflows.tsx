import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiGet, apiPost, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { FaPlus, FaPlay, FaCheckCircle, FaTimesCircle, FaClock, FaSpinner, FaCogs } from "react-icons/fa";

interface Workflow {
  id: string;
  name: string;
  description: string | null;
  status: string;
  current_version_id: string | null;
}

export default function Workflows() {
  const { auth } = useAuth();
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);

  const loadWorkflows = async () => {
    if (!auth) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<any>("/workflows", auth.token);
      const items = Array.isArray(data) ? data : (data?.items || []);
      setWorkflows(items);
    } catch (err: any) {
      setError(err instanceof ApiError
        ? `Backend Error (${err.status}): ${err.message}`
        : (err.message || "Failed to load workflows."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadWorkflows(); }, [auth]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim() || !auth) return;
    setCreating(true);
    setError(null);
    try {
      const newWf = await apiPost<Workflow>("/workflows", auth.token, {
        name: newName,
        description: "Auto-deployed with starter graph",
      });
      setWorkflows([newWf, ...workflows]);
      setNewName("");
    } catch (err: any) {
      setError(err instanceof ApiError
        ? `Failed to create (${err.status}): ${err.message}`
        : "Failed to create workflow.");
    } finally {
      setCreating(false);
    }
  };

  const handleRun = async (wfId: string) => {
    if (!auth) return;
    setRunningId(wfId);
    setError(null);
    try {
      await apiPost<{ run_id: string; status: string }>(
        `/workflows/${wfId}/runs`,
        auth.token,
        { input_data: {} },
      );
      setTimeout(() => navigate("/executions"), 1500);
    } catch (err: any) {
      setError(err instanceof ApiError
        ? `Run failed (${err.status}): ${err.message}`
        : "Run failed.");
      setRunningId(null);
    }
  };

  return (
    <div className="max-w-7xl mx-auto flex flex-col h-full">
      <div className="mb-6 flex justify-between items-end shrink-0">
        <div>
          <h1 className="text-3xl font-serif text-gray-900 tracking-tight">Workflows</h1>
          <p className="text-gray-500 text-sm mt-1">Deploy, run, and monitor your event-driven workflows.</p>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-xl mb-6 text-sm flex items-center gap-2">
          <FaTimesCircle /> {error}
        </div>
      )}

      <form onSubmit={handleCreate} className="bg-white p-2 rounded-2xl border border-gray-200 shadow-sm flex items-center gap-2 mb-8">
        <div className="flex-1 flex items-center gap-3 px-4">
          <FaPlus className="text-gray-400" />
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Name your new workflow (e.g., Customer Onboarding)"
            className="w-full bg-transparent border-none outline-none text-gray-900 placeholder-gray-400 h-10"
            style={{ backgroundColor: 'transparent', border: 'none', boxShadow: 'none' }}
          />
        </div>
        <button
          type="submit"
          disabled={creating || !newName.trim()}
          className="bg-[#0a5c36] hover:bg-[#08482a] text-white font-medium px-6 py-2.5 rounded-xl transition-colors disabled:opacity-50 text-sm shrink-0 flex items-center gap-2"
        >
          {creating ? <FaSpinner className="animate-spin" /> : "Create Workflow"}
        </button>
      </form>

      <div className="flex-1 bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden flex flex-col">
        <div className="p-4 border-b border-gray-100 bg-gray-50 flex items-center justify-between shrink-0">
          <h2 className="text-sm font-semibold text-gray-700">Workflows ({workflows.length})</h2>
          <button onClick={loadWorkflows} className="text-xs text-[#0a5c36] hover:underline font-medium">
            Refresh
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-400">
              <FaSpinner className="animate-spin mr-2" /> Loading workflows...
            </div>
          ) : workflows.length === 0 && !error ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-400 p-8 text-center">
              <div className="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center text-2xl text-gray-300 mb-4">
                <FaCogs />
              </div>
              <p className="text-lg font-medium text-gray-600 mb-1">No workflows yet</p>
              <p className="text-sm">Create your first workflow above to start automating with AI.</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {workflows.map((wf) => (
                <div key={wf.id} className="p-5 flex items-center justify-between hover:bg-gray-50 transition-colors">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-xl bg-gray-100 text-gray-500 flex items-center justify-center">
                      <FaCogs />
                    </div>
                    <div>
                      <div className="text-base font-semibold text-gray-900">{wf.name}</div>
                      <div className="text-xs text-gray-500 mt-0.5">{wf.description || "No description"}</div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <StatusBadge status={wf.status} />
                    {wf.status === "active" && (
                      <button
                        onClick={() => handleRun(wf.id)}
                        disabled={runningId === wf.id}
                        className="flex items-center gap-1.5 bg-[#0a5c36] hover:bg-[#08482a] text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                      >
                        {runningId === wf.id ? <FaSpinner className="animate-spin" /> : <FaPlay />}
                        Run
                      </button>
                    )}
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

function StatusBadge({ status }: { status: string }) {
  if (status === "active" || status === "deployed")
    return <span className="flex items-center gap-1.5 text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-md text-xs font-semibold border border-emerald-200"><FaCheckCircle /> {status}</span>;
  if (status === "draft")
    return <span className="flex items-center gap-1.5 text-gray-600 bg-gray-100 px-2.5 py-1 rounded-md text-xs font-semibold border border-gray-200"><FaClock /> {status}</span>;
  return <span className="flex items-center gap-1.5 text-blue-700 bg-blue-50 px-2.5 py-1 rounded-md text-xs font-semibold border border-blue-200">{status}</span>;
}