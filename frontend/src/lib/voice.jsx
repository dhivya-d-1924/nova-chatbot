import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useSettings } from "./settings";

export const LANGUAGES = [
  ["en-IN", "English (India)"], ["en-US", "English (US)"], ["en-GB", "English (UK)"],
  ["ta-IN", "தமிழ் (Tamil)"], ["hi-IN", "हिन्दी (Hindi)"], ["te-IN", "తెలుగు (Telugu)"],
  ["ml-IN", "മലയാളം (Malayalam)"], ["kn-IN", "ಕನ್ನಡ (Kannada)"], ["es-ES", "Español"],
  ["fr-FR", "Français"], ["de-DE", "Deutsch"], ["ja-JP", "日本語"], ["zh-CN", "中文"], ["ar-SA", "العربية"],
];

const getRecognition = () => (typeof window === "undefined" ? null : window.SpeechRecognition || window.webkitSpeechRecognition || null);
const canSpeak = () => typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;


/** Turn Markdown into something that sounds natural when read aloud. */
export function stripForSpeech(md) {
  return md
    .replace(/```[\s\S]*?```/g, " (code block omitted) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\[\d+(?:\s*,\s*\d+)*\]/g, "")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[*_~>|]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function chunkText(text, max = 200) {
  const parts = text.match(/[^.!?।。]+[.!?।。]*\s*/g) || [text];
  const out = [];
  let cur = "";
  for (const p of parts) {
    if ((cur + p).length > max && cur) { out.push(cur.trim()); cur = ""; }
    cur += p;
    while (cur.length > max) { out.push(cur.slice(0, max)); cur = cur.slice(max); }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

const VoiceContext = createContext({
  lang: "en-US", setLang: () => {}, autoRead: false, setAutoRead: () => {},
  input: false, output: false, speakingId: null, speak: () => {}, stop: () => {},
});
export const useVoice = () => useContext(VoiceContext);

export function VoiceProvider({ children }) {
  // Preferences come from the synced user settings, so they follow the user across devices.
  const synced = useSettings();
  const saved = synced?.settings || {};
  const navLang = (typeof navigator !== "undefined" && navigator.language) || "en-US";
  const lang = saved.voice_lang || (LANGUAGES.some(([c]) => c === navLang) ? navLang : "en-US");
  const autoRead = !!saved.auto_read;
  const [speakingId, setSpeakingId] = useState(null);
  const token = useRef(0);
  const update = synced?.update;

  const setLang = useCallback((l) => update?.({ voice_lang: l }), [update]);
  const setAutoRead = useCallback((on) => update?.({ auto_read: on }), [update]);

  const stop = useCallback(() => {
    token.current += 1;
    if (canSpeak()) window.speechSynthesis.cancel();
    setSpeakingId(null);
  }, []);

  const speak = useCallback((id, markdown) => {
    if (!canSpeak()) return;
    stop();
    const mine = token.current;
    const chunks = chunkText(stripForSpeech(markdown));
    if (!chunks.length) return;
    const voices = window.speechSynthesis.getVoices?.() || [];
    const voice = voices.find((v) => v.lang === lang) || voices.find((v) => v.lang?.startsWith(lang.slice(0, 2)));
    setSpeakingId(id);
    chunks.forEach((c, i) => {
      const u = new window.SpeechSynthesisUtterance(c);
      u.lang = lang;
      if (voice) u.voice = voice;
      const done = () => { if (token.current === mine && i === chunks.length - 1) setSpeakingId(null); };
      u.onend = done;
      u.onerror = done;
      window.speechSynthesis.speak(u);
    });
  }, [lang, stop]);

  useEffect(() => () => { if (canSpeak()) window.speechSynthesis.cancel(); }, []);

  const value = useMemo(() => ({
    lang, setLang, autoRead, setAutoRead, input: !!getRecognition(), output: canSpeak(), speakingId, speak, stop,
  }), [lang, setLang, autoRead, setAutoRead, speakingId, speak, stop]);
  return <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>;
}

const ERRORS = {
  "not-allowed": "Microphone access is blocked. Allow it in your browser's address bar and try again.",
  "service-not-allowed": "Microphone access is blocked. Allow it in your browser's address bar and try again.",
  "no-speech": "I didn't hear anything. Try again.",
  "audio-capture": "No microphone was found.",
  network: "Voice input needs an internet connection.",
  "language-not-supported": "This browser doesn't support voice input in that language.",
};

/** Voice dictation. onResult receives the full transcript so far (interim words included). */
export function useDictation(lang, onResult) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState(null);
  const rec = useRef(null);
  const cb = useRef(onResult);
  cb.current = onResult;

  const stop = useCallback(() => { rec.current?.stop(); }, []);
  const start = useCallback(() => {
    const R = getRecognition();
    if (!R) return;
    setError(null);
    const r = new R();
    r.lang = lang;
    r.interimResults = true;
    r.continuous = true;
    r.onresult = (e) => cb.current(Array.from(e.results).map((x) => x[0].transcript).join(" ").replace(/\s+/g, " ").trim());
    r.onerror = (e) => { if (e.error !== "aborted") setError(ERRORS[e.error] || `Voice input error (${e.error}).`); };
    r.onend = () => { setListening(false); rec.current = null; };
    rec.current = r;
    try { r.start(); setListening(true); } catch { setListening(false); }
  }, [lang]);

  useEffect(() => () => rec.current?.abort(), []);
  return { listening, error, start, stop, supported: !!getRecognition() };
}
