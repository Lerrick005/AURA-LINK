import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Workflows from "./pages/Workflows";
import WorkflowDetail from "./pages/WorkflowDetail";
import RunDetail from "./pages/RunDetail";
import Executions from "./pages/Executions";
import Inbox from "./pages/Inbox";
import Channels from "./pages/Channels";
import Knowledge from "./pages/Knowledge";
import AIAssistant from "./pages/AIAssistant";

function Protected({ children }: { children: React.ReactNode }) {
  const { auth } = useAuth();
  if (!auth) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protected><Layout /></Protected>}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/workflows/:id" element={<WorkflowDetail />} />
        <Route path="/runs" element={<Executions />} />

        <Route path="/executions" element={<Executions />} />
        <Route path="/runs/:runId" element={<RunDetail />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/channels" element={<Channels />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/assistant" element={<AIAssistant />} />
      </Route>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}