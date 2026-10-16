import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  Archive, ArchiveRestore, Brain, Download, LayoutDashboard, LogOut, MessageSquare, MoreHorizontal, Pencil, Pin, PinOff,
  Plus, Search, Trash2, UserCog, X,
} from "lucide-react";
import Avatar from "./Avatar";
import IconButton from "./IconButton";
import { formatWhen, useDebounced, useDialog, useMediaQuery } from "../lib/hooks";
import { overlay, spring } from "../lib/motion";

function ItemMenu({ conv, anchor, onClose, onRename, onPin, onArchive, onExport, onDelete }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.querySelector("button")?.focus();
    const onDown = (e) => ref.current && !ref.current.contains(e.target) && onClose();
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [onClose]);
  const item = "flex min-h-11 w-full items-center gap-2.5 rounded-lg px-3 text-left text-sm hover:bg-ink/10 focus-visible:bg-ink/10";
  return (
    <div ref={ref} role="menu" aria-label={`Actions for ${conv.title}`} style={menuPosition(anchor)}
      className="glass fixed z-[70] w-56 rounded-xl bg-panel p-1 shadow-2xl">
      <button type="button" role="menuitem" className={item} onClick={onRename}><Pencil size={15} /> Rename</button>
      {!conv.archived && (
        <button type="button" role="menuitem" className={item} onClick={onPin}>
          {conv.pinned ? <><PinOff size={15} /> Unpin</> : <><Pin size={15} /> Pin to top</>}
        </button>
      )}
      <button type="button" role="menuitem" className={item} onClick={onArchive}>
        {conv.archived ? <><ArchiveRestore size={15} /> Restore from archive</> : <><Archive size={15} /> Archive</>}
      </button>
      <button type="button" role="menuitem" className={item} onClick={() => onExport("md")}><Download size={15} /> Export as Markdown</button>
      <button type="button" role="menuitem" className={item} onClick={() => onExport("json")}><Download size={15} /> Export as JSON</button>
      <button type="button" role="menuitem" className={`${item} text-red-400`} onClick={onDelete}><Trash2 size={15} /> Delete</button>
    </div>
  );
}

/** Place the menu next to its button, flipping upward near the bottom of the screen. Rendered in a portal so the
 * scrolling list can't clip it. */
function menuPosition(rect) {
  const height = 6 * 44 + 8;
  const right = Math.max(8, window.innerWidth - rect.right);
  const below = rect.bottom + 4;
  return below + height < window.innerHeight - 8 ? { top: below, right } : { top: Math.max(8, rect.top - height - 4), right };
}

function ConversationItem({ c, active, onSelect, chat, onDelete }) {
  const [menu, setMenu] = useState(null); // anchor rect when open
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(c.title);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (!editing) setTitle(c.title); }, [c.title, editing]);

  const saveTitle = async (e) => {
    e?.preventDefault();
    const t = title.trim();
    if (!t || t === c.title) { setEditing(false); return; }
    setSaving(true);
    const ok = await chat.updateConversation(c.id, { title: t }, "Renamed.");
    setSaving(false);
    if (ok) setEditing(false);
  };

  if (editing) {
    return (
      <li>
        <form onSubmit={saveTitle} className="flex items-center gap-1 rounded-xl bg-ink/10 p-1">
          <label htmlFor={`rename-${c.id}`} className="sr-only">New title</label>
          <input
            id={`rename-${c.id}`} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus disabled={saving}
            onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
            className="min-h-10 min-w-0 flex-1 rounded-lg border border-line/15 bg-bg/60 px-2.5 text-base focus:border-accent focus:outline-none sm:text-sm"
          />
          <button type="submit" disabled={saving || !title.trim()} className="min-h-10 rounded-lg px-2.5 text-xs font-semibold text-volt hover:bg-ink/10 disabled:opacity-50">Save</button>
        </form>
      </li>
    );
  }
  return (
    <li className="group relative">
      <button
        type="button" onClick={() => onSelect(c.id)} aria-current={active ? "page" : undefined}
        className={`flex min-h-11 w-full items-center gap-2.5 rounded-xl py-2 pl-3 pr-12 text-left text-sm transition-colors ${
          active ? "bg-accent/20 text-ink" : "text-muted hover:bg-ink/10 hover:text-ink"}`}
      >
        {c.pinned ? <Pin size={15} className="shrink-0 text-volt" aria-hidden="true" /> : <MessageSquare size={16} className="shrink-0" aria-hidden="true" />}
        <span className="min-w-0 flex-1">
          <span className="block truncate">{c.title}</span>
          <span className="block text-[11px] text-muted/80">{formatWhen(c.updated_at)}{c.pinned && <span className="sr-only"> · pinned</span>}</span>
        </span>
      </button>
      <button
        type="button" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu((m) => (m ? null : r)); }}
        onPointerDown={(e) => e.stopPropagation()} aria-haspopup="menu" aria-expanded={!!menu} aria-label={`More actions for ${c.title}`}
        className="absolute right-1 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-lg text-muted opacity-100 transition hover:bg-ink/10 hover:text-ink md:opacity-0 md:focus-visible:opacity-100 md:group-hover:opacity-100 aria-expanded:opacity-100"
      >
        <MoreHorizontal size={17} />
      </button>
      {menu && createPortal(
        <ItemMenu
          conv={c} anchor={menu} onClose={() => setMenu(null)}
          onRename={() => { setMenu(null); setEditing(true); }}
          onPin={() => { setMenu(null); chat.updateConversation(c.id, { pinned: !c.pinned }, c.pinned ? "Unpinned." : "Pinned to the top."); }}
          onArchive={() => { setMenu(null); chat.updateConversation(c.id, { archived: !c.archived }, c.archived ? "Restored." : "Archived."); }}
          onExport={(f) => { setMenu(null); chat.exportConversation(c.id, f); }}
          onDelete={() => { setMenu(null); onDelete(c); }}
        />, document.body,
      )}
    </li>
  );
}

function Panel({ chat, activeId, onSelect, onNew, onDelete, onClose, showClose, user, onLogout, onAdmin, onMemory, onAccount, view }) {
  const [search, setSearch] = useState(chat.query);
  const debounced = useDebounced(search, 300);
  const { setQuery } = chat;
  useEffect(() => { setQuery(debounced.trim()); }, [debounced, setQuery]);
  const { conversations, listLoading: loading, listError, showArchived } = chat;

  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex items-center gap-3 p-4 pb-3">
        <Avatar size={36} />
        <div className="min-w-0 flex-1">
          <p className="font-display text-base font-semibold leading-tight">Nova</p>
          <p className="text-xs text-muted">AI agent</p>
        </div>
        {showClose && <IconButton label="Close menu" onClick={onClose}><X size={20} /></IconButton>}
      </div>

      <div className="space-y-2 px-4 pb-3">
        <motion.button
          type="button" onClick={onNew} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }}
          className="btn-gradient flex min-h-11 w-full items-center justify-center gap-2 rounded-xl text-sm font-semibold shadow-glow transition-shadow hover:shadow-lift"
        >
          <Plus size={18} /> New chat
        </motion.button>
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true" />
          <label htmlFor="chat-search" className="sr-only">Search conversations</label>
          <input
            id="chat-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search chats"
            className="min-h-11 w-full rounded-xl border border-line/15 bg-ink/5 pl-9 pr-3 text-base placeholder:text-muted/70 focus:border-accent focus:outline-none sm:text-sm"
          />
        </div>
        <div role="tablist" aria-label="Which chats to show" className="grid grid-cols-2 gap-1 rounded-xl bg-ink/5 p-1 text-xs font-medium">
          {[["Chats", false], ["Archived", true]].map(([label, value]) => (
            <button key={label} type="button" role="tab" aria-selected={showArchived === value} onClick={() => chat.setShowArchived(value)}
              className={`min-h-9 rounded-lg transition-colors ${showArchived === value ? "bg-panel text-ink shadow" : "text-muted hover:text-ink"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <nav aria-label="Chat history" className="thin-scroll min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {listError && <p role="alert" className="mx-2 mb-2 rounded-lg bg-red-500/10 p-2 text-xs text-red-400">{listError}</p>}
        {loading ? (
          <div className="space-y-2 px-2" aria-busy="true" aria-label="Loading conversations">
            {[0, 1, 2, 3].map((i) => <div key={i} className="h-11 animate-pulse rounded-xl bg-ink/10" style={{ animationDelay: `${i * 120}ms` }} />)}
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-muted">
            {chat.query ? `No chats match “${chat.query}”.` : showArchived ? "No archived chats." : "No conversations yet. Start a new chat and it will show up here."}
          </p>
        ) : (
          <ul className="space-y-1">
            {conversations.map((c) => (
              <ConversationItem key={c.id} c={c} active={c.id === activeId} onSelect={onSelect} chat={chat} onDelete={onDelete} />
            ))}
          </ul>
        )}
      </nav>

      <div className="border-t border-line/10 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <div className="mb-2 grid grid-cols-2 gap-1">
          <button type="button" onClick={onMemory} className="flex min-h-11 items-center gap-2 rounded-xl px-3 text-sm text-muted transition-colors hover:bg-ink/10 hover:text-ink">
            <Brain size={16} /> Memory
          </button>
          <button type="button" onClick={onAccount} className="flex min-h-11 items-center gap-2 rounded-xl px-3 text-sm text-muted transition-colors hover:bg-ink/10 hover:text-ink">
            <UserCog size={16} /> Account
          </button>
        </div>
        {user.role === "admin" && (
          <button
            type="button" onClick={onAdmin} aria-current={view === "admin" ? "page" : undefined}
            className={`mb-2 flex min-h-11 w-full items-center gap-2.5 rounded-xl px-3 text-sm transition-colors ${view === "admin" ? "bg-accent/20 text-ink" : "text-muted hover:bg-ink/10 hover:text-ink"}`}
          >
            <LayoutDashboard size={16} /> Admin dashboard
          </button>
        )}
        <div className="flex items-center gap-3 px-1">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent/20 font-display text-sm font-semibold uppercase text-accent" aria-hidden="true">
            {user.name.slice(0, 1)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{user.name}</p>
            <p className="truncate text-xs text-muted">{user.email}</p>
          </div>
          <IconButton label="Sign out" onClick={onLogout} side="top"><LogOut size={18} /></IconButton>
        </div>
      </div>
    </div>
  );
}

/** Static column on desktop; slide-out drawer on tablet and mobile. */
export default function Sidebar({ open, onClose, ...props }) {
  const desktop = useMediaQuery("(min-width: 1024px)");
  const drawer = useRef(null);
  useDialog(!desktop && open, onClose, drawer);

  if (desktop) {
    return (
      <aside className="glass h-full w-72 shrink-0 border-y-0 border-l-0 xl:w-80" aria-label="Conversations">
        <Panel {...props} />
      </aside>
    );
  }
  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div {...overlay} onClick={onClose} className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" aria-hidden="true" />
          <motion.aside
            ref={drawer} role="dialog" aria-modal="true" aria-label="Conversations"
            initial={{ x: "-100%" }} animate={{ x: 0 }} exit={{ x: "-100%" }} transition={spring}
            className="glass fixed inset-y-0 left-0 z-50 w-[min(20rem,88vw)] bg-panel pl-[env(safe-area-inset-left)] pt-[env(safe-area-inset-top)] shadow-2xl"
          >
            <Panel {...props} showClose onClose={onClose} />
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
