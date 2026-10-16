import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useDialog } from "../lib/hooks";
import { overlay, spring } from "../lib/motion";

export default function ConfirmModal({ open, title, body, confirmLabel, onConfirm, onCancel }) {
  const dialog = useRef(null);
  const [busy, setBusy] = useState(false);
  useDialog(open, onCancel, dialog);
  useEffect(() => { if (!open) setBusy(false); }, [open]);
  const confirm = async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); } };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          {...overlay}
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          onMouseDown={(e) => e.target === e.currentTarget && onCancel()}
        >
          <motion.div
            ref={dialog}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            aria-describedby="confirm-body"
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.97 }}
            transition={spring}
            className="glass w-full max-w-sm rounded-2xl bg-panel p-6 shadow-2xl"
          >
            <h2 id="confirm-title" className="font-display text-lg font-semibold">{title}</h2>
            <p id="confirm-body" className="mt-2 text-sm text-muted">{body}</p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button" data-autofocus
                onClick={onCancel}
                className="min-h-11 rounded-xl border border-line/15 px-4 text-sm font-medium transition-colors hover:bg-ink/10"
              >
                Cancel
              </button>
              <button
                type="button" disabled={busy}
                onClick={confirm}
                className="min-h-11 rounded-xl bg-red-500 px-4 text-sm font-semibold text-white transition-colors hover:bg-red-600 disabled:opacity-60"
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
