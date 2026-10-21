import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { MessageCircle, RefreshCw, WifiOff } from "lucide-react";
import AccountPanel from "./components/AccountPanel";
import AdminDashboard from "./components/AdminDashboard";
import AuthScreen from "./components/AuthScreen";
import Avatar from "./components/Avatar";
import ChatWindow from "./components/ChatWindow";
import ConfirmModal from "./components/ConfirmModal";
import DocumentsPanel from "./components/DocumentsPanel";
import MemoryPanel from "./components/MemoryPanel";
import Sidebar from "./components/Sidebar";
import StudyPanel from "./components/StudyPanel";
import { useAuth } from "./hooks/useAuth";
import { useChat } from "./hooks/useChat";
import { useDocuments } from "./hooks/useDocuments";
import { useTools } from "./hooks/useTools";
import { api } from "./lib/api";
import { useOnline } from "./lib/hooks";
import { spring } from "./lib/motion";
import { SettingsProvider } from "./lib/settings";
import { ToastProvider, useToast } from "./lib/toast";
import { VoiceProvider } from "./lib/voice";

function ChatApp({ user, auth }) {
  const toast = useToast();
  const notify = useCallback((kind, text) => toast.show(kind, text), [toast]);
  const chat = useChat({ onNotice: notify });
  const docs = useDocuments(chat.setDocIds, notify);
  const tools = useTools(chat.setTools, chat.setAgent);
  const online = useOnline();
  const [docsOpen, setDocsOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [study, setStudy] = useState(null); // null = closed, {} = subject list, { id, tab } = a subject
  const [health, setHealth] = useState(null);
  const [view, setView] = useState("chat"); // chat | admin
  const [menuOpen, setMenuOpen] = useState(false);
  const [minimized, setMinimized] = useState(false);
  const [confirm, setConfirm] = useState(null); // { type: "clear" } | { type: "delete", conv }

  const checkHealth = useCallback(() => {
    api.health().then((h) => setHealth(h)).catch(() => setHealth({ unreachable: true, configured: false }));
  }, []);
  useEffect(() => { checkHealth(); }, [checkHealth]);
  // When the server or network was down, keep checking so the banner clears on its own.
  useEffect(() => {
    if (!health?.unreachable && online) return undefined;
    const t = setInterval(checkHealth, 10_000);
    return () => clearInterval(t);
  }, [health, online, checkHealth]);
  useEffect(() => { if (online) checkHealth(); }, [online, checkHealth]);

  const closeMenu = useCallback(() => setMenuOpen(false), []);
  const cancelConfirm = useCallback(() => setConfirm(null), []);
  const toChat = (fn) => (arg) => { fn(arg); setView("chat"); setMenuOpen(false); };

  const onConfirm = async () => {
    const c = confirm;
    setConfirm(null);
    if (c?.type === "clear") await chat.clearChat();
    if (c?.type === "delete") await chat.removeConversation(c.conv.id);
  };

  return (
    <div className="flex h-dvh w-full flex-col">
      {(!online || health?.unreachable) && (
        <div role="status" className="flex shrink-0 items-center justify-center gap-2 bg-amber-500/15 px-3 pb-1.5 pt-[max(0.375rem,env(safe-area-inset-top))] text-center text-xs font-medium text-amber-300">
          <WifiOff size={14} aria-hidden="true" />
          {!online ? "You're offline. Messages will send when you reconnect." : "Can't reach the server. Retrying…"}
        </div>
      )}
      <div className="min-h-0 flex-1">
        <AnimatePresence mode="wait">
          {minimized ? (
            <motion.div key="min" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
              <Avatar size={64} float />
              <p className="text-muted">Your chat is minimized.</p>
            </motion.div>
          ) : (
            <motion.div key="app" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.97 }}
              transition={{ duration: 0.35, ease: "easeOut" }} className="flex h-full">
              <Sidebar
                open={menuOpen}
                onClose={closeMenu}
                chat={chat}
                activeId={view === "chat" ? chat.activeId : null}
                onSelect={toChat(chat.selectConversation)}
                onNew={toChat(chat.newChat)}
                onDelete={(conv) => setConfirm({ type: "delete", conv })}
                user={user}
                view={view}
                onLogout={auth.logout}
                onAdmin={() => { setView("admin"); setMenuOpen(false); }}
                onMemory={() => { setMemoryOpen(true); setMenuOpen(false); }}
                onAccount={() => { setAccountOpen(true); setMenuOpen(false); }}
                onStudy={() => { setStudy({}); setMenuOpen(false); }}
              />
              {view === "admin" && user.role === "admin" ? (
                <AdminDashboard me={user} onBack={() => setView("chat")} onOpenMenu={() => setMenuOpen(true)} />
              ) : (
                <ChatWindow
                  chat={chat}
                  health={health}
                  online={online}
                  docCount={docs.selected.length}
                  tools={tools}
                  onOpenDocs={() => setDocsOpen(true)}
                  onOpenMenu={() => setMenuOpen(true)}
                  onMinimize={() => setMinimized(true)}
                  onRequestClear={() => setConfirm({ type: "clear" })}
                  onOpenStudy={(id) => setStudy(id ? { id } : {})}
                />
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <AnimatePresence>
        {minimized && (
          <motion.button
            type="button" onClick={() => setMinimized(false)} aria-label="Open chat"
            initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={spring}
            whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.92 }}
            className="btn-gradient fixed bottom-[max(1.5rem,env(safe-area-inset-bottom))] right-[max(1.5rem,env(safe-area-inset-right))] z-40 flex h-16 w-16 items-center justify-center rounded-full shadow-glow"
          >
            <motion.span className="absolute inset-0 rounded-full bg-accent/60" animate={{ scale: [1, 1.5], opacity: [0.6, 0] }}
              transition={{ duration: 1.8, repeat: Infinity, ease: "easeOut" }} />
            <MessageCircle size={28} className="relative" />
          </motion.button>
        )}
      </AnimatePresence>

      <MemoryPanel open={memoryOpen} onClose={() => setMemoryOpen(false)} onChanged={tools.reload} />
      <DocumentsPanel open={docsOpen} onClose={() => setDocsOpen(false)} docs={docs} />
      <StudyPanel open={!!study} target={study} onClose={() => setStudy(null)}
        onOpenChat={async (conv) => { setStudy(null); setView("chat"); await chat.refreshList(); chat.selectConversation(conv.id); }} />
      <AccountPanel open={accountOpen} onClose={() => setAccountOpen(false)} user={user} auth={auth} />

      <ConfirmModal
        open={!!confirm}
        title={confirm?.type === "delete" ? "Delete this conversation?" : "Clear this chat?"}
        body={confirm?.type === "delete"
          ? `"${confirm.conv.title}" and all its messages will be removed permanently.`
          : "All messages in this conversation will be removed permanently."}
        confirmLabel={confirm?.type === "delete" ? "Delete" : "Clear chat"}
        onConfirm={onConfirm}
        onCancel={cancelConfirm}
      />
    </div>
  );
}

function Root() {
  const auth = useAuth();
  if (auth.loading) {
    return <div className="flex h-dvh items-center justify-center" aria-busy="true" aria-label="Loading"><Avatar size={56} busy /></div>;
  }
  if (auth.bootError) {
    return (
      <div role="alert" className="flex h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
        <Avatar size={56} />
        <p className="max-w-sm text-sm text-muted">{auth.bootError}</p>
        <button type="button" onClick={auth.boot} className="btn-gradient inline-flex min-h-11 items-center gap-2 rounded-xl px-5 text-sm font-semibold shadow-glow">
          <RefreshCw size={16} /> Try again
        </button>
      </div>
    );
  }
  if (!auth.user) return <AuthScreen auth={auth} />;
  // key = user id, so chat state resets completely when someone else signs in
  return (
    <SettingsProvider key={auth.user.id} initial={auth.user.settings}>
      <VoiceProvider>
        <ChatApp user={auth.user} auth={auth} />
      </VoiceProvider>
    </SettingsProvider>
  );
}

export default function App() {
  return <ToastProvider><Root /></ToastProvider>;
}
