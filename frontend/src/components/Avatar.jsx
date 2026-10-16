import { motion } from "framer-motion";
import { Sparkles } from "lucide-react";

/** Floating AI avatar with a glowing, pulsing ring. */
export default function Avatar({ size = 40, float = false, busy = false }) {
  return (
    <motion.div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      animate={float ? { y: [0, -8, 0] } : undefined}
      transition={float ? { duration: 4, repeat: Infinity, ease: "easeInOut" } : undefined}
      aria-hidden="true"
    >
      <motion.span
        className="absolute inset-0 rounded-full bg-accent/50"
        animate={{ scale: busy ? [1, 1.5] : [1, 1.35], opacity: [0.5, 0] }}
        transition={{ duration: busy ? 1.1 : 2.4, repeat: Infinity, ease: "easeOut" }}
      />
      <span
        className="absolute inset-0 rounded-full p-[2px] shadow-glow"
        style={{ background: "conic-gradient(from 180deg, rgb(var(--accent)), rgb(var(--volt)), rgb(var(--accent)))" }}
      >
        <span className="flex h-full w-full items-center justify-center rounded-full bg-bg">
          <Sparkles size={size * 0.46} className="text-volt" />
        </span>
      </span>
    </motion.div>
  );
}
