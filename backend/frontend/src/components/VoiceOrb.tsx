import { useEffect, useState } from "react";

export default function VoiceOrb() {
  const [pulse, setPulse] = useState(false);

  // Simulate a listening state pulse
  useEffect(() => {
    const interval = setInterval(() => setPulse(p => !p), 1500);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="relative flex items-center justify-center w-full h-full min-h-[200px]">
      {/* Outer glow rings */}
      <div className={`absolute inset-0 rounded-full border border-blue-500/30 transition-all duration-1000 ${pulse ? 'scale-110 opacity-0' : 'scale-100 opacity-100'}`}></div>
      <div className={`absolute inset-4 rounded-full border border-blue-500/20 transition-all duration-1000 delay-100 ${pulse ? 'scale-110 opacity-0' : 'scale-100 opacity-100'}`}></div>
      
      {/* Core Orb */}
      <div className="relative w-32 h-32 rounded-full bg-gradient-to-br from-blue-400 via-blue-600 to-indigo-900 shadow-[0_0_50px_rgba(59,130,246,0.6)] flex items-center justify-center cursor-pointer group hover:scale-105 transition-transform">
        {/* Inner glass reflection */}
        <div className="absolute top-2 left-4 w-10 h-10 bg-white/20 rounded-full blur-md"></div>
        
        {/* Microphone Icon */}
        <svg className="w-10 h-10 text-white/90 group-hover:text-white transition-colors" fill="currentColor" viewBox="0 0 24 24">
          <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
          <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
        </svg>
      </div>
    </div>
  );
}