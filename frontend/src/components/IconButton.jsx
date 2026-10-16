import { motion } from "framer-motion";
import Tooltip from "./Tooltip";

export default function IconButton({ label, onClick, children, disabled, side, align, className = "", ...rest }) {
  return (
    <Tooltip label={label} side={side} align={align}>
      <motion.button
        type="button"
        aria-label={label}
        onClick={onClick}
        disabled={disabled}
        whileHover={disabled ? undefined : { scale: 1.08 }}
        whileTap={disabled ? undefined : { scale: 0.92 }}
        className={`inline-flex h-11 w-11 items-center justify-center rounded-xl text-muted transition-colors hover:bg-ink/10 hover:text-ink disabled:opacity-40 md:h-10 md:w-10 ${className}`}
        {...rest}
      >
        {children}
      </motion.button>
    </Tooltip>
  );
}
