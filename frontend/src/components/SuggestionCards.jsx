import { motion } from "framer-motion";
import { BookOpen, Code2, Lightbulb, PenLine } from "lucide-react";
import { fadeUp, stagger } from "../lib/motion";

const SUGGESTIONS = [
  { icon: BookOpen, title: "Explain a concept", hint: "Hash tables, with a simple analogy",
    prompt: "Explain how a hash table works, using a simple everyday analogy." },
  { icon: Code2, title: "Write code", hint: "A debounce helper in JavaScript",
    prompt: "Write a debounce function in JavaScript and show a short usage example." },
  { icon: Lightbulb, title: "Help me solve a problem", hint: "Work through it step by step",
    prompt: "I have a problem I'd like to work through. Ask me questions one at a time until you understand it, then suggest a solution." },
  { icon: PenLine, title: "Draft something", hint: "A clear, friendly email",
    prompt: "Help me write a short, friendly email asking a colleague for a project update." },
];

export default function SuggestionCards({ onPick, disabled }) {
  return (
    <motion.ul
      variants={stagger(0.08, 0.5)}
      initial="hidden"
      animate="show"
      className="grid w-full max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2"
    >
      {SUGGESTIONS.map(({ icon: Icon, title, hint, prompt }) => (
        <motion.li key={title} variants={fadeUp}>
          <motion.button
            type="button"
            disabled={disabled}
            onClick={() => onPick(prompt)}
            whileHover={{ y: -4 }}
            whileTap={{ scale: 0.98 }}
            className="glass group flex min-h-[72px] w-full items-center gap-3 rounded-2xl p-4 text-left transition-shadow duration-300 hover:shadow-lift hover:border-accent/50 disabled:opacity-50"
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent transition-colors group-hover:bg-accent group-hover:text-white">
              <Icon size={20} />
            </span>
            <span className="min-w-0">
              <span className="block font-display text-sm font-semibold">{title}</span>
              <span className="block truncate text-sm text-muted">{hint}</span>
            </span>
          </motion.button>
        </motion.li>
      ))}
    </motion.ul>
  );
}
