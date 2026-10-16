export const spring = { type: "spring", stiffness: 380, damping: 30 };

export const stagger = (delay = 0.08, start = 0.15) => ({
  hidden: {},
  show: { transition: { staggerChildren: delay, delayChildren: start } },
});

export const fadeUp = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] } },
};

export const messageIn = {
  initial: { opacity: 0, y: 12, scale: 0.98 },
  animate: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.3, ease: "easeOut" } },
};

export const overlay = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
};
