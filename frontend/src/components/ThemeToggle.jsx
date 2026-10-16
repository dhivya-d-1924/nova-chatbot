import { AnimatePresence, motion } from "framer-motion";
import { Moon, Sun } from "lucide-react";
import { useSettings } from "../lib/settings";
import { resolveTheme, useTheme } from "../lib/theme";
import IconButton from "./IconButton";

/** Synced to the account when signed in; local on the sign-in screen. */
export default function ThemeToggle({ className = "" }) {
  const synced = useSettings();
  const [localTheme, localToggle] = useTheme();
  const theme = synced ? resolveTheme(synced.settings.theme) : localTheme;
  const next = theme === "dark" ? "light" : "dark";
  const toggle = synced ? () => synced.update({ theme: next }) : localToggle;
  return (
    <IconButton label={`Switch to ${next} theme`} onClick={toggle} className={className}>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={theme}
          initial={{ rotate: -80, opacity: 0, scale: 0.6 }} animate={{ rotate: 0, opacity: 1, scale: 1 }}
          exit={{ rotate: 80, opacity: 0, scale: 0.6 }} transition={{ duration: 0.2 }} className="flex"
        >
          {theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}
        </motion.span>
      </AnimatePresence>
    </IconButton>
  );
}
