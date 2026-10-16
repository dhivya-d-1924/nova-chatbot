/** CSS-only tooltip for pointer devices: shows on hover and keyboard focus. Not rendered into layout until shown,
 * so tooltips at the screen edge never cause horizontal scrolling. Touch devices rely on the aria-label instead. */
export default function Tooltip({ label, side = "bottom", align = "center", children }) {
  const pos = side === "top" ? "bottom-full mb-2" : "top-full mt-2";
  const x = align === "end" ? "right-0" : align === "start" ? "left-0" : "left-1/2 -translate-x-1/2";
  return (
    <span className="group relative inline-flex">
      {children}
      <span
        role="tooltip"
        className={`pointer-events-none absolute z-50 hidden whitespace-nowrap rounded-md bg-ink px-2 py-1 text-xs font-medium text-bg [@media(hover:hover)]:group-hover:block [@media(hover:hover)]:group-focus-within:block ${x} ${pos}`}
      >
        {label}
      </span>
    </span>
  );
}
