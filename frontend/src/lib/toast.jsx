import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

const ToastContext = createContext({ show: () => {}, success: () => {}, error: () => {}, info: () => {} });
const ICONS = { success: CheckCircle2, error: AlertTriangle, info: Info };
const COLORS = { success: "text-emerald-400", error: "text-red-400", info: "text-volt" };

/** Small notifications for confirmed outcomes ("Saved", "Deleted") and recoverable errors. */
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const seq = useRef(0);
  const dismiss = useCallback((id) => setItems((x) => x.filter((t) => t.id !== id)), []);
  const show = useCallback((kind, text, ms = kind === "error" ? 7000 : 3500) => {
    seq.current += 1;
    const id = seq.current;
    setItems((x) => [...x.slice(-3), { id, kind, text }]);
    setTimeout(() => dismiss(id), ms);
  }, [dismiss]);
  const value = useMemo(() => ({
    show, success: (t) => show("success", t), error: (t) => show("error", t), info: (t) => show("info", t),
  }), [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-[max(0.75rem,env(safe-area-inset-top))] z-[80] flex flex-col items-center gap-2 px-3">
        <AnimatePresence initial={false}>
          {items.map((t) => {
            const Icon = ICONS[t.kind];
            return (
              <motion.div
                key={t.id} layout initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
                role={t.kind === "error" ? "alert" : "status"}
                className="glass pointer-events-auto flex w-full max-w-md items-start gap-2.5 rounded-xl bg-panel p-3 text-sm shadow-2xl"
              >
                <Icon size={18} className={`mt-0.5 shrink-0 ${COLORS[t.kind]}`} aria-hidden="true" />
                <p className="min-w-0 flex-1 break-words">{t.text}</p>
                <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss notification"
                  className="-m-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-ink/10 hover:text-ink">
                  <X size={15} />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
