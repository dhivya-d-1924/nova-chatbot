import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamChat, streamResume } from "../lib/api";

/** All chat state and server interaction. status: idle | loading | streaming | error */
export function useChat({ onNotice } = {}) {
  const [conversations, setConversations] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(null);
  const [showArchived, setShowArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState(null);
  const [activeSubject, setActiveSubject] = useState(null); // study chat: { id, name }
  const [messages, setMessages] = useState([]);
  const [threadLoading, setThreadLoading] = useState(false);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const activeRef = useRef(null);
  const abortRef = useRef(null);
  const retryRef = useRef(null); // set when a send failed before anything was stored
  const docIdsRef = useRef([]);
  const toolsRef = useRef(false);
  const agentRef = useRef("");
  const lastSendRef = useRef(null);
  const messagesRef = useRef([]);
  const listArgs = useRef({ archived: false, q: "" });
  messagesRef.current = messages;
  const notice = useRef(onNotice);
  notice.current = onNotice;
  const setTools = useCallback((on) => { toolsRef.current = on; }, []);
  const setAgent = useCallback((id) => { agentRef.current = id; }, []);
  const setDocIds = useCallback((ids) => { docIdsRef.current = ids; }, []);

  const setActive = useCallback((id) => {
    activeRef.current = id;
    setActiveId(id);
  }, []);

  const refreshList = useCallback(async () => {
    const args = listArgs.current;
    try {
      const list = await api.listConversations(args);
      if (listArgs.current === args) { setConversations(list); setListError(null); }
    } catch (e) {
      setListError(e.message);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    listArgs.current = { archived: showArchived, q: query };
    setListLoading(true);
    refreshList();
  }, [showArchived, query, refreshList]);

  /** Reload the open conversation from the server: the source of truth after an interrupted stream.
   * If the server hasn't stored the stopped reply yet, the local partial copy stays visible. */
  const syncThread = useCallback(async (cid) => {
    try {
      const data = await api.getConversation(cid);
      if (activeRef.current !== cid) return;
      setMessages((local) => {
        const server = data.messages;
        const partial = local.find((m) => String(m.id).startsWith("partial-"));
        if (partial && server.length && server[server.length - 1].role === "user") return [...server, partial];
        return server;
      });
    } catch { /* keep what we have; the error banner explains */ }
  }, []);

  const run = useCallback(async (cid, payload, tmpId, seedSteps = []) => {
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setStatus("loading");
    setMessages((m) => [...m, { id: "pending", role: "assistant", content: "", steps: seedSteps, created_at: Date.now() * 1000 }]);
    try {
      const handlers = {
        signal: controller.signal,
        onDelta: (t) => {
          setStatus("streaming");
          setMessages((m) => m.map((x) => (x.id === "pending" ? { ...x, content: x.content + t } : x)));
        },
        onStep: (step) => {
          setStatus("streaming");
          setMessages((m) => m.map((x) => {
            if (x.id !== "pending") return x;
            const steps = x.steps || [];
            return { ...x, steps: steps.some((s) => s.id === step.id) ? steps.map((s) => (s.id === step.id ? { ...step } : s)) : [...steps, { ...step }] };
          }));
        },
        onDone: (msg) => setMessages((m) => m.map((x) => (x.id === "pending" ? msg : x))),
        // the server confirms the stored question: swap the temporary id for the real one (needed to edit it)
        onUser: (u) => setMessages((m) => m.map((x) => (tmpId && x.id === tmpId ? { ...x, id: u.id, created_at: u.created_at } : x))),
      };
      if (payload.resume) await streamResume({ ...payload.resume, ...handlers });
      else {
        await streamChat({ conversationId: cid, ...payload, docIds: docIdsRef.current, useTools: toolsRef.current,
          agent: agentRef.current, ...handlers });
      }
      setStatus("idle");
    } catch (e) {
      if (e.name === "AbortError") {
        // Stop pressed: the server keeps the partial reply; show it until we re-sync.
        setMessages((m) => m.flatMap((x) =>
          x.id !== "pending" ? [x] : x.content || x.steps?.length ? [{ ...x, id: `partial-${Date.now()}` }] : []));
        setStatus("idle");
        // the server saves the partial reply once it notices the disconnect; pick up its real id when it has
        setTimeout(() => syncThread(cid), 1500);
        setTimeout(() => syncThread(cid), 5000);
      } else {
        if (e.saved) {
          setMessages((m) => m.map((x) => (x.id === "pending" ? e.saved : x)));
        } else {
          setMessages((m) => m.filter((x) => x.id !== "pending"));
        }
        if (e.preStream && payload.message && !payload.editMessageId) {
          setMessages((m) => m.filter((x) => x.id !== tmpId));
          retryRef.current = lastSendRef.current;
        } else if (!e.preStream) {
          syncThread(cid); // the server may have stored part of the reply: show exactly what it has
        }
        setError(e.message);
        setStatus("error");
      }
    } finally {
      abortRef.current = null;
      refreshList();
    }
  }, [refreshList, syncThread]);

  const ensureConversation = useCallback(async () => {
    let cid = activeRef.current;
    if (!cid) {
      cid = (await api.createConversation()).id;
      setActive(cid);
    }
    return cid;
  }, [setActive]);

  const send = useCallback(async (text, attachments = []) => {
    const message = (text || "").trim();
    if (!message && !attachments.length) return;
    lastSendRef.current = { message, attachments };
    let cid;
    try {
      cid = await ensureConversation();
    } catch (e) {
      retryRef.current = lastSendRef.current;
      setError(e.message);
      setStatus("error");
      return;
    }
    retryRef.current = null;
    const tmpId = `tmp-${Date.now()}`;
    setMessages((m) => [...m, { id: tmpId, role: "user", content: message, created_at: Date.now() * 1000,
      images: attachments.map((a) => ({ preview: a.preview })) }]);
    await run(cid, { message, attachmentIds: attachments.map((a) => a.id) }, tmpId);
  }, [run, ensureConversation]);

  /** Edit an earlier question: later messages are replaced by a new answer. */
  const editMessage = useCallback(async (messageId, text) => {
    const cid = activeRef.current;
    const message = (text || "").trim();
    if (!cid || !message) return;
    const before = messagesRef.current;
    const idx = before.findIndex((m) => m.id === messageId);
    if (idx < 0) return;
    setMessages(before.slice(0, idx + 1).map((m, i) => (i === idx ? { ...m, content: message, edited_at: Date.now() * 1000 } : m)));
    await run(cid, { message, editMessageId: messageId });
  }, [run]);

  const regenerate = useCallback(() => {
    const cid = activeRef.current;
    if (!cid) return undefined;
    setMessages((m) => (m.length && m[m.length - 1].role === "assistant" ? m.slice(0, -1) : m));
    return run(cid, { regenerate: true });
  }, [run]);

  const retry = useCallback(
    () => (retryRef.current ? send(retryRef.current.message, retryRef.current.attachments || []) : regenerate()),
    [send, regenerate],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const resolveAction = useCallback((messageId, pendingId, approve) => {
    const cid = activeRef.current;
    if (!cid) return undefined;
    const seed = (messagesRef.current.find((x) => x.id === messageId)?.steps || []).map((s) => ({ ...s }));
    setMessages((m) => m.filter((x) => x.id !== messageId));
    return run(cid, { resume: { pendingId, approve } }, undefined, seed);
  }, [run]);

  const rate = useCallback(async (id, rating) => {
    let previous = 0;
    setMessages((m) => m.map((x) => {
      if (x.id !== id) return x;
      previous = x.rating || 0;
      return { ...x, rating: previous === rating ? 0 : rating };
    }));
    try {
      await api.rate(id, previous === rating ? 0 : rating);
    } catch (e) {
      setMessages((m) => m.map((x) => (x.id === id ? { ...x, rating: previous } : x)));
      notice.current?.("error", `Couldn't save your rating: ${e.message}`);
    }
  }, []);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    setActive(null);
    setActiveSubject(null);
    setMessages([]);
    setError(null);
    setStatus("idle");
    retryRef.current = null;
  }, [setActive]);

  const selectConversation = useCallback(async (id) => {
    if (id === activeRef.current) return;
    abortRef.current?.abort();
    setActive(id);
    setActiveSubject(null);
    setMessages([]);
    setError(null);
    setStatus("idle");
    retryRef.current = null;
    setThreadLoading(true);
    try {
      const data = await api.getConversation(id);
      if (activeRef.current === id) { setMessages(data.messages); setActiveSubject(data.subject || null); }
    } catch (e) {
      if (activeRef.current === id) { setError(e.message); setStatus("error"); }
    } finally {
      if (activeRef.current === id) setThreadLoading(false);
    }
  }, [setActive]);

  const removeConversation = useCallback(async (id) => {
    try {
      await api.deleteConversation(id);
      if (activeRef.current === id) newChat();
      await refreshList();
      notice.current?.("success", "Conversation deleted.");
    } catch (e) {
      notice.current?.("error", `Couldn't delete: ${e.message}`);
    }
  }, [newChat, refreshList]);

  const updateConversation = useCallback(async (id, changes, okText) => {
    try {
      const updated = await api.updateConversation(id, changes);
      setConversations((list) => list.map((c) => (c.id === id ? updated : c)));
      await refreshList();
      if (okText) notice.current?.("success", okText);
      return true;
    } catch (e) {
      notice.current?.("error", e.message);
      return false;
    }
  }, [refreshList]);

  const exportConversation = useCallback(async (id, format) => {
    try {
      await api.exportConversation(id, format);
    } catch (e) {
      notice.current?.("error", `Export failed: ${e.message}`);
    }
  }, []);

  const clearChat = useCallback(async () => {
    const cid = activeRef.current;
    if (!cid) { setMessages([]); return; }
    abortRef.current?.abort();
    try {
      await api.clearConversation(cid);
      setMessages([]);
      setError(null);
      setStatus("idle");
      refreshList();
      notice.current?.("success", "Chat cleared.");
    } catch (e) {
      notice.current?.("error", `Couldn't clear the chat: ${e.message}`);
    }
  }, [refreshList]);

  return {
    conversations, listLoading, listError, activeId, activeSubject, messages, threadLoading, status, error, showArchived, query,
    setShowArchived, setQuery, refreshList,
    send, editMessage, regenerate, retry, stop, rate, setDocIds, setTools, setAgent, resolveAction, newChat,
    selectConversation, removeConversation, updateConversation, exportConversation, clearChat,
  };
}
