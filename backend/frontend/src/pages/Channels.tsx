import { FaWhatsapp, FaInstagram, FaFacebookMessenger, FaTiktok, FaEnvelope, FaPlus } from "react-icons/fa";

export default function Channels() {
  const channelList = [
    { name: "WhatsApp", icon: <FaWhatsapp />, color: "text-[#25D366]", bg: "bg-[#25D366]/10", connected: true },
    { name: "Instagram", icon: <FaInstagram />, color: "text-[#E1306C]", bg: "bg-[#E1306C]/10", connected: true },
    { name: "Messenger", icon: <FaFacebookMessenger />, color: "text-[#0084FF]", bg: "bg-[#0084FF]/10", connected: true },
    { name: "TikTok", icon: <FaTiktok />, color: "text-black", bg: "bg-gray-200", connected: false },
    { name: "Email", icon: <FaEnvelope />, color: "text-[#EA4335]", bg: "bg-[#EA4335]/10", connected: true },
  ];

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="mb-6 shrink-0">
        <h1 className="text-3xl font-light tracking-tight text-gray-900 mb-1 font-serif">Channels</h1>
        <p className="text-gray-500 text-sm">Connect your communication platforms to Auralink.</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {channelList.map(c => (
          <div key={c.name} className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm hover:shadow-md transition-shadow flex flex-col items-center text-center">
            <div className={`w-16 h-16 rounded-2xl ${c.bg} ${c.color} flex items-center justify-center text-3xl mb-4`}>
              {c.icon}
            </div>
            <h3 className="font-semibold text-gray-900 mb-1">{c.name}</h3>
            <p className="text-xs text-gray-500 mb-4">{c.connected ? "Connected and listening" : "Not connected"}</p>
            <button className={`w-full py-2 rounded-lg text-sm font-medium transition-colors ${c.connected ? 'bg-gray-100 text-gray-700 hover:bg-gray-200' : 'bg-[#1a5f4a] text-white hover:bg-[#144a3a]'}`}>
              {c.connected ? "Manage" : "Connect"}
            </button>
          </div>
        ))}
        
        <button className="border-2 border-dashed border-gray-300 rounded-2xl p-5 flex flex-col items-center justify-center text-gray-400 hover:border-gray-400 hover:text-gray-600 transition-colors min-h-[220px]">
          <FaPlus className="text-2xl mb-2" />
          <span className="text-sm font-medium">Add Custom Channel</span>
        </button>
      </div>
    </div>
  );
}