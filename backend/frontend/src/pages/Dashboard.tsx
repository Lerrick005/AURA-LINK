import { useEffect, useState } from "react";
import { apiGet } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { FaWhatsapp, FaInstagram, FaFacebookMessenger, FaTiktok, FaEnvelope, FaBolt, FaRandom, FaUserAlt, FaBookOpen, FaHeart } from "react-icons/fa";
import VoiceOrb from "../components/VoiceOrb";

export default function Dashboard() {
  const { auth } = useAuth();
  const [stats, setStats] = useState({ totalRuns: 0, completed: 0, escalated: 0, tokens: 0 });

  useEffect(() => {
    if (!auth) return;
    apiGet<any>("/runs?limit=100", auth.token).then(data => {
      let completed = 0;
      let escalated = 0;
      let tokens = 0;
      data.items.forEach((r: any) => {
        if (r.status === "completed") completed++;
        if (r.status === "escalated") escalated++;
        tokens += (r.tokens_in + r.tokens_out);
      });
      setStats({ completed, escalated, totalRuns: data.total, tokens });
    }).catch(err => console.error("Failed to load runs:", err));
  }, [auth]);

  return (
    <div className="max-w-7xl mx-auto flex flex-col h-full relative">
      {/* Header */}
      <div className="mb-6 flex justify-between items-end shrink-0 relative z-20">
        <h1 className="text-3xl font-serif text-gray-900 tracking-tight">Every channel, one AI teammate</h1>
        <div className="text-xs bg-[#e6f0eb] text-[#0a5c36] font-medium px-3 py-1.5 rounded-full flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-[#0a5c36] animate-pulse"></span>
          Live · {stats.totalRuns} runs processed
        </div>
      </div>

      {/* Main Grid Layout */}
      <div className="flex-1 grid grid-cols-12 gap-6 min-h-0 items-center relative">

        {/* SVG Connector Network (Background) */}
        <div className="absolute inset-0 pointer-events-none z-0">
          {/* viewBox 0 0 1000 500 ensures lines scale perfectly with the container */}
          <svg className="w-full h-full" viewBox="0 0 1000 500" preserveAspectRatio="none">
            <defs>
              <linearGradient id="glow-in" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#0a5c36" stopOpacity="0.2" />
                <stop offset="100%" stopColor="#34d399" stopOpacity="0.8" />
              </linearGradient>
              <linearGradient id="glow-out" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#34d399" stopOpacity="0.8" />
                <stop offset="100%" stopColor="#0a5c36" stopOpacity="0.2" />
              </linearGradient>
              <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
              </filter>
            </defs>

            {/* 
              Lines coming from Left (Channels). 
              Start at X=200 (card edge). Y matches the 5 cards (50, 150, 250, 350, 450).
              Curve control points pull them towards the center (X=500, Y=250).
            */}
            <path d="M 200 50 C 350 50, 350 250, 500 250" fill="none" stroke="url(#glow-in)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 200 150 C 350 150, 350 250, 500 250" fill="none" stroke="url(#glow-in)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 200 250 C 350 250, 350 250, 500 250" fill="none" stroke="url(#glow-in)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 200 350 C 350 350, 350 250, 500 250" fill="none" stroke="url(#glow-in)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 200 450 C 350 450, 350 250, 500 250" fill="none" stroke="url(#glow-in)" strokeWidth="2" filter="url(#glow)" />

            {/* 
              Lines going to Right (Features). 
              Start at center (X=500, Y=250) and end at X=800 (card edge).
            */}
            <path d="M 500 250 C 650 250, 650 50, 800 50" fill="none" stroke="url(#glow-out)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 500 250 C 650 250, 650 150, 800 150" fill="none" stroke="url(#glow-out)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 500 250 C 650 250, 650 250, 800 250" fill="none" stroke="url(#glow-out)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 500 250 C 650 250, 650 350, 800 350" fill="none" stroke="url(#glow-out)" strokeWidth="2" filter="url(#glow)" />
            <path d="M 500 250 C 650 250, 650 450, 800 450" fill="none" stroke="url(#glow-out)" strokeWidth="2" filter="url(#glow)" />
          </svg>
        </div>

        {/* Left Column: Channels */}
        <div className="col-span-3 flex flex-col justify-between h-[500px] relative z-10 py-2">
          <ChannelCard name="WhatsApp" count="1,248 msgs" icon={<FaWhatsapp />} color="bg-[#25D366] text-white" />
          <ChannelCard name="Instagram" count="863 msgs" icon={<FaInstagram />} color="bg-gradient-to-tr from-[#f9ce34] via-[#ee2a7b] to-[#6228d7] text-white" />
          <ChannelCard name="Messenger" count="412 msgs" icon={<FaFacebookMessenger />} color="bg-[#0084FF] text-white" />
          <ChannelCard name="TikTok" count="1,102 msgs" icon={<FaTiktok />} color="bg-black text-white" />
          <ChannelCard name="Email" count="329 msgs" icon={<FaEnvelope />} color="bg-[#EA4335] text-white" />
        </div>

        {/* Center Column: The AI Orb */}
        <div className="col-span-6 h-full flex flex-col items-center justify-center relative z-10">
          <div className="relative w-[340px] h-[500px] bg-[#0a0e1a] rounded-[2rem] border border-gray-800 shadow-[0_0_40px_rgba(52,211,153,0.15)] overflow-hidden flex flex-col items-center">
            <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-blue-500/10 via-transparent to-transparent opacity-50"></div>
            
            <div className="text-[10px] font-bold tracking-[0.2em] text-blue-200 uppercase mt-6 mb-2 z-10">AI Assistant</div>

            {/* The Animated Orb */}
            <div className="w-full h-48 z-10">
              <VoiceOrb />
            </div>

            {/* Chat bubble */}
            <div className="bg-[#111827]/90 backdrop-blur-md border border-gray-700/50 rounded-xl p-4 mx-6 mt-4 text-sm text-gray-200 z-10 shadow-lg w-[280px] flex-1 overflow-y-auto">
              <div className="mb-3">
                <span className="text-blue-400 font-medium">System:</span> Auralink engine initialized.
              </div>
              <div className="mb-3">
                <span className="text-emerald-400 font-medium">Status:</span> Connected to backend.
              </div>
              <div className="text-xs text-gray-400 mt-4 border-t border-gray-700 pt-2">
                {stats.totalRuns > 0
                  ? `Processed ${stats.totalRuns} tasks using ${stats.tokens} tokens.`
                  : "Waiting for first workflow trigger..."}
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: REAL STATS */}
        <div className="col-span-3 flex flex-col justify-between h-[500px] relative z-10 py-2">
          <OutcomeCard title="Instant answers" count={`${stats.completed} resolved`} icon={<FaBolt />} color="bg-teal-50 text-teal-600" />
          <OutcomeCard title="Smart routing" count={`${stats.totalRuns} routed`} icon={<FaRandom />} color="bg-blue-50 text-blue-600" />
          <OutcomeCard title="Human handoff" count={`${stats.escalated} escalated`} icon={<FaUserAlt />} color="bg-orange-50 text-orange-600" />
          <OutcomeCard title="Knowledge match" count={`${stats.tokens} tokens`} icon={<FaBookOpen />} color="bg-purple-50 text-purple-600" />
          <OutcomeCard title="Sentiment watch" count="Active" icon={<FaHeart />} color="bg-red-50 text-red-600" />
        </div>
      </div>
    </div>
  );
}

function ChannelCard({ name, count, icon, color }: any) {
  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-4 flex items-center gap-4 shadow-sm hover:shadow-md transition-shadow cursor-pointer relative group">
      <div className="absolute -right-[7px] top-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full bg-[#34d399] border-2 border-white z-20 group-hover:scale-125 transition-transform shadow-[0_0_10px_rgba(52,211,153,0.8)]"></div>
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-xl shadow-sm shrink-0 ${color}`}>
        {icon}
      </div>
      <div>
        <div className="font-semibold text-sm text-gray-900">{name}</div>
        <div className="text-[11px] text-gray-500">{count}</div>
      </div>
    </div>
  );
}

function OutcomeCard({ title, count, icon, color }: any) {
  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-4 flex items-center gap-4 shadow-sm relative">
      <div className="absolute -left-[7px] top-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full bg-[#34d399] border-2 border-white z-20 shadow-[0_0_10px_rgba(52,211,153,0.8)]"></div>
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-xl shadow-sm shrink-0 ${color}`}>
        {icon}
      </div>
      <div>
        <div className="font-semibold text-sm text-gray-900">{title}</div>
        <div className="text-[11px] text-gray-500">{count}</div>
      </div>
    </div>
  );
}
