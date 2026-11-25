import { FaSearch } from "react-icons/fa";

export default function Inbox() {
  const messages = [
    { id: 1, channel: "WhatsApp", user: "Kemi Adeyemi", text: "No, no, no, for now, thank you so much.", time: "2m ago", unread: true },
    { id: 2, channel: "Email", user: "Acme Corp", text: "Re: Invoice #4029", time: "1h ago", unread: false },
    { id: 3, channel: "Instagram", user: "John Doe", text: "Do you ship to the UK?", time: "3h ago", unread: false },
  ];

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="mb-6 shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-light tracking-tight text-gray-900 mb-1 font-serif">Inbox</h1>
          <p className="text-gray-500 text-sm">Manage all your incoming conversations in one place.</p>
        </div>
      </div>

      <div className="flex-1 bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden flex flex-col min-h-0">
        <div className="p-4 border-b border-gray-100 flex items-center gap-3">
          <FaSearch className="text-gray-400" />
          <input type="text" placeholder="Search messages..." className="flex-1 text-sm focus:outline-none" />
        </div>
        <div className="flex-1 overflow-y-auto divide-y divide-gray-100">
          {messages.map(m => (
            <div key={m.id} className={`p-4 flex gap-4 hover:bg-gray-50 cursor-pointer transition-colors ${m.unread ? 'bg-blue-50/30' : ''}`}>
              <div className="w-10 h-10 rounded-full bg-gray-200 flex items-center justify-center shrink-0 font-bold text-gray-600">
                {m.user[0]}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex justify-between items-baseline mb-1">
                  <div className="font-medium text-sm text-gray-900">{m.user} <span className="text-xs text-gray-400 font-normal ml-1">via {m.channel}</span></div>
                  <div className="text-xs text-gray-500">{m.time}</div>
                </div>
                <p className={`text-sm truncate ${m.unread ? 'text-gray-900 font-medium' : 'text-gray-500'}`}>{m.text}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}