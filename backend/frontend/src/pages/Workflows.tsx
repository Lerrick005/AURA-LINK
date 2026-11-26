import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiGet, apiPost, ApiError } from "../api/client";

interface Workflow {
  id: string;
  name: string;
  description: string | null;
  status: string;
  current_version_id: string;
  created_at: string;
  updated_at: string;
}

export default function Workflows() {
  const { auth } = useAuth();
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  const loadWorkflows = async () => {
    if (!auth) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<any>('/workflows', auth.token);
      const list: Workflow[] = Array.isArray(data) ? data : (data.items || data.data || []);
      setWorkflows(list);
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 401) {
        navigate("/login");
        return;
      }
      setError(err.message || "Failed to load workflows");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWorkflows();
  }, [auth?.token]);

  const handleCreate = async () => {
    if (!auth || !newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await apiPost("/workflows", auth.token, { name: newName.trim() });
      setNewName("");
      await loadWorkflows();
    } catch (err: any) {
      setError(err.message || "Failed to create workflow");
    } finally {
      setCreating(false);
    }
  };

  const handleRun = async (wf: Workflow) => {
    if (!auth) return;
    try {
      await apiPost(`/workflows/${wf.id}/runs`, auth.token, {
        input_data: { message: "Hello! Reply with SUCCESS." },
      });
      navigate("/executions");
    } catch (err: any) {
      setError(err.message || "Failed to run workflow");
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Workflows</h1>
        <p className="text-sm text-gray-600 mt-1">Deploy, run, and monitor your event-driven workflows.</p>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 text-sm rounded-md border border-red-200">
          {error}
        </div>
      )}

      <div className="mb-6 p-4 bg-white rounded-xl shadow-sm border border-gray-200">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Name your new workflow (e.g., Customer Onboarding)"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="flex-1 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
          />
          <button
            onClick={handleCreate}
            disabled={creating || !newName.trim()}
            className="px-4 py-2 bg-green-700 hover:bg-green-800 disabled:bg-gray-300 text-white font-medium rounded-md transition-colors"
          >
            {creating ? "Creating..." : "Create Workflow"}
          </button>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200">
        <div className="flex justify-between items-center px-4 py-3 border-b border-gray-200">
          <h2 className="font-medium text-gray-900">
            Workflows ({workflows.length})
          </h2>
          <button
            onClick={loadWorkflows}
            className="text-sm text-green-700 hover:text-green-900"
          >
            Refresh
          </button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : workflows.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No workflows yet. Create one above to get started.
          </div>
        ) : (
          <ul className="divide-y divide-text-gray-200">
            {workflows.map((wf) => (
              <li key={wf.id} className="flex items-center justify-between px-4 py-3 hover:bg-gray-50">
                <div>
                  <div className="font-medium text-gray-900">{wf.name}</div>
                  <div className="text-xs text-gray-500">
                    {wf.description || "No description"} ÷ {wf.status}
                  </div>
                </div>
                <button
                  onClick={() => handleRun(wf)}
                  className="px-3 py-1 text-sm bg-gray-900 hover:bg-gray-700 text-white rounded-md"
                >
                  Run
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
