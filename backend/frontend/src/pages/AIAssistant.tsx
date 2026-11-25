import VoiceOrb from "../components/VoiceOrb";

export default function AIAssistant() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center min-h-0">
       <div className="relative group w-[400px] max-w-full flex flex-col h-[500px]">
          <div className="absolute inset-0 bg-[#0a0e1a] rounded-[2rem] blur-xl opacity-10 transform scale-95" />
          <div className="relative bg-[#0a0e1a] border border-gray-800 rounded-[2rem] p-6 shadow-2xl flex flex-col items-center h-full overflow-hidden">
            <div className="absolute inset-0 opacity-20 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-blue-500/20 via-transparent to-transparent" />
            
            <div className="relative z-10 w-full flex flex-col items-center h-full">
              <span className="text-[10px] font-bold tracking-[0.2em] text-gray-400 uppercase mb-2 shrink-0">
                Direct AI Link
              </span>
              
              <div className="shrink-0 py-4">
                <VoiceOrb />
              </div>

              <div className="w-full mt-2 bg-[#111827] border border-gray-800 rounded-xl p-4 flex-1 overflow-y-auto shadow-inner text-left">
                <p className="text-sm text-gray-400 text-center mt-8">Speak to the AI directly here. Use the microphone above to start.</p>
              </div>
            </div>
          </div>
        </div>
    </div>
  );
}