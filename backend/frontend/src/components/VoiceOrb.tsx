import { useEffect, useRef, useState } from "react";
import { apiPost } from "../api/client";
import { useAuth } from "../auth/AuthContext";

type Props = {
  onResult?: (r: { transcript: string; reply: string }) => void;
  onError?: (msg: string) => void;
};

export default function VoiceOrb({ onResult, onError }: Props) {
  const { auth } = useAuth();
  const [pulse, setPulse] = useState(false);
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const recognitionRef = useRef<any>(null);

  // cosmetic pulse
  useEffect(() => {
    const interval = setInterval(() => setPulse((p) => !p), 1500);
    return () => clearInterval(interval);
  }, []);

  const startListening = () => {
    if (listening || busy) return;
    if (!auth?.token) {
      onError?.("Not logged in");
      return;
    }

    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!SR) {
      onError?.("Speech recognition not supported in this browser (try Chrome/Edge)");
      return;
    }

    const recognition = new SR();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;

    recognition.onstart = () => setListening(true);
    recognition.onend = () => setListening(false);
    recognition.onerror = (e: any) => {
      setListening(false);
      onError?.(`mic error: ${e?.error ?? "unknown"}`);
    };
    recognition.onresult = async (event: any) => {
      const transcript = event.results?.[0]?.[0]?.transcript?.trim();
      if (!transcript) return;
      setBusy(true);
      try {
        const res = await apiPost<{ reply: string }>(
          "/assistant/chat",
          auth.token,
          { message: transcript },
        );
        const reply = res?.reply ?? "";
        onResult?.({ transcript, reply });

        if (reply && "speechSynthesis" in window) {
          window.speechSynthesis.cancel();
          window.speechSynthesis.speak(new SpeechSynthesisUtterance(reply));
        }
      } catch (err: any) {
        onError?.(err?.message ?? "assistant call failed");
      } finally {
        setBusy(false);
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch (e: any) {
      setListening(false);
      onError?.(`could not start: ${e?.message ?? e}`);
    }
  };

  const stopListening = () => {
    try {
      recognitionRef.current?.stop();
    } catch {
      /* ignore */
    }
  };

  const handleClick = () => {
    if (listening) stopListening();
    else startListening();
  };

  const ringColor = listening ? "border-red-500/50" : "border-blue-500/30";
  const innerRing = listening ? "border-red-500/30" : "border-blue-500/20";
  const coreColor = listening
    ? "from-red-400 via-red-600 to-rose-900 shadow-[0_0_60px_rgba(239,68,68,0.7)]"
    : "from-blue-400 via-blue-600 to-indigo-900 shadow-[0_0_50px_rgba(59,130,246,0.6)]";

  return (
    <div className="relative flex items-center justify-center w-full h-full min-h-[200px]">
      <div className={`absolute inset-0 rounded-full border ${ringColor} transition-all duration-1000 ${pulse ? "scale-110 opacity-0" : "scale-100 opacity-100"}`}></div>
      <div className={`absolute inset-4 rounded-full border ${innerRing} transition-all duration-1000 delay-100 ${pulse ? "scale-110 opacity-0" : "scale-100 opacity-100"}`}></div>

      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        aria-label={listening ? "Stop listening" : "Start listening"}
        className={`relative w-32 h-32 rounded-full bg-gradient-to-br ${coreColor} flex items-center justify-center cursor-pointer group hover:scale-105 transition-transform disabled:opacity-60 disabled:cursor-wait`}
      >
        <div className="absolute top-2 left-4 w-10 h-10 bg-white/20 rounded-full blur-md"></div>

        {busy ? (
          <svg className="w-10 h-10 text-white/90 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>
          </svg>
        ) : (
          <svg className="w-10 h-10 text-white/90 group-hover:text-white transition-colors" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
            <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
          </svg>
        )}
      </button>
    </div>
  );
}
