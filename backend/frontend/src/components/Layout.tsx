import { Outlet, Link, useLocation, useNavigate } from "react-router-dom";
import { FaHome, FaInbox, FaPlug, FaBook, FaRobot, FaSignOutAlt, FaCogs, FaBolt, FaSearch, FaBell, FaExchangeAlt } from "react-icons/fa";
import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { auth, logout } = useAuth();

  const email = "admin@acme.com";
  const initials = email.substring(0, 2).toUpperCase();

  const navItems = [
    { path: "/dashboard", label: "Overview", icon: <FaHome /> },
    { path: "/workflows", label: "Workflows", icon: <FaCogs /> },
    { path: "/executions", label: "Executions", icon: <FaBolt /> },
    { path: "/inbox", label: "Inbox", icon: <FaInbox /> },
    { path: "/channels", label: "Channels", icon: <FaPlug /> },
    { path: "/knowledge", label: "Knowledge", icon: <FaBook /> },
    { path: "/assistant", label: "AI Assistant", icon: <FaRobot /> },
  ];

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <div className="flex h-screen w-full bg-[#f7f6f3] overflow-hidden">
      {/* Sidebar */}
      <aside className="w-[260px] border-r border-gray-200 bg-white flex flex-col shrink-0">
        <div className="p-6">
          <h1 className="text-2xl font-serif text-[#0a5c36] flex items-center gap-2 font-bold">
            <div className="w-8 h-8 bg-[#0a5c36] text-white rounded-lg flex items-center justify-center font-sans font-bold">A</div>
            Auralink
          </h1>
        </div>

        <div className="px-4 pb-4">
          <div className="bg-gray-50 border border-gray-200 rounded-xl p-3 flex items-center justify-between cursor-pointer">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-teal-100 text-teal-700 rounded-lg flex items-center justify-center font-bold">S</div>
              <div>
                <div className="text-sm font-semibold">Shopnova</div>
                <div className="text-[11px] text-gray-500">Growth plan</div>
              </div>
            </div>
            <FaExchangeAlt className="text-gray-400 text-xs" />
          </div>
        </div>

        <nav className="flex-1 px-4 space-y-1 overflow-y-auto">
          <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2 mt-4 px-2">Workspace</div>
          {navItems.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                location.pathname === item.path
                  ? "bg-[#e6f0eb] text-[#0a5c36]"
                  : "text-gray-600 hover:bg-gray-50"
              }`}
            >
              <div className="flex items-center gap-3">
                <span className="text-lg">{item.icon}</span>
                {item.label}
              </div>
            </Link>
          ))}
        </nav>

        <div className="p-4 border-t border-gray-100">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-[#1a5f4a] text-white rounded-full flex items-center justify-center text-xs font-bold">
                {initials}
              </div>
              <div className="text-xs font-medium text-gray-700">{email}</div>
            </div>
            <button onClick={handleLogout} title="Log out" className="text-gray-400 hover:text-red-500 transition-colors">
              <FaSignOutAlt />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Topbar */}
        <header className="h-16 border-b border-gray-200 bg-white flex items-center justify-between px-6 shrink-0">
          <div className="flex items-center gap-2 bg-gray-100 px-4 py-2 rounded-xl w-[400px]">
            <FaSearch className="text-gray-400" />
            <input
              type="text"
              placeholder="Search conversations, customers, docs..."
              className="bg-transparent border-none text-sm outline-none w-full placeholder-gray-500 text-gray-900"
              style={{ backgroundColor: 'transparent', border: 'none', boxShadow: 'none', color: '#111827' }}
            />
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-sm font-medium text-[#0a5c36] bg-[#e6f0eb] px-3 py-1.5 rounded-full">
              <span className="w-2 h-2 rounded-full bg-[#0a5c36] animate-pulse"></span>
              AI online
            </div>
            <div className="relative cursor-pointer hover:bg-gray-50 p-2 rounded-full">
              <FaBell className="text-xl text-gray-400" />
              <span className="absolute top-1 right-1 w-2 h-2 bg-red-500 rounded-full border border-white"></span>
            </div>
            <div className="flex items-center gap-3 border-l border-gray-200 pl-4">
              <div className="w-8 h-8 bg-[#1a5f4a] text-white rounded-full flex items-center justify-center text-xs font-bold">
                {initials}
              </div>
              <div>
                <div className="text-sm font-semibold text-gray-900">{email}</div>
                <div className="text-[11px] text-gray-500">Support lead</div>
              </div>
            </div>
          </div>
        </header>

        {/* Page Content */}
        <div className="flex-1 overflow-auto p-8 relative">
          <Outlet />
        </div>
      </main>
    </div>
  );
}