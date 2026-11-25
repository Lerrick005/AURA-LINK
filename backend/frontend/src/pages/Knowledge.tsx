import { FaFileAlt, FaUpload, FaSearch } from "react-icons/fa";

export default function Knowledge() {
  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="mb-6 shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-light tracking-tight text-gray-900 mb-1 font-serif">Knowledge</h1>
          <p className="text-gray-500 text-sm">Upload documents and policies for your AI to reference.</p>
        </div>
        <button className="bg-[#1a5f4a] text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 shadow-sm hover:bg-[#144a3a]">
          <FaUpload /> Upload Document
        </button>
      </div>

      <div className="bg-white border border-gray-200 rounded-2xl shadow-sm flex-1 flex flex-col min-h-0">
        <div className="p-4 border-b border-gray-100 flex items-center gap-3 shrink-0">
          <FaSearch className="text-gray-400" />
          <input type="text" placeholder="Search documents..." className="flex-1 text-sm focus:outline-none" />
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wider border-b border-gray-200">
                <th className="px-6 py-3 font-medium">Name</th>
                <th className="px-6 py-3 font-medium">Type</th>
                <th className="px-6 py-3 font-medium">Size</th>
                <th className="px-6 py-3 font-medium">Added</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-sm text-gray-700">
              {[1, 2, 3].map(i => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-6 py-4 flex items-center gap-3">
                    <div className="w-8 h-8 rounded bg-blue-50 text-blue-500 flex items-center justify-center"><FaFileAlt /></div>
                    <span className="font-medium">Refund_Policy_v{i}.pdf</span>
                  </td>
                  <td className="px-6 py-4 text-gray-500">PDF Document</td>
                  <td className="px-6 py-4 text-gray-500">1.2 MB</td>
                  <td className="px-6 py-4 text-gray-500">Oct {20+i}, 2026</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}