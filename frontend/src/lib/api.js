/**
 * API client.
 * - Authentication uses an HttpOnly session cookie set by the server (JavaScript can't read it).
 * - A CSRF token from /auth/me or sign-in is kept in memory and sent on every state-changing request.
 * - Safe (GET) requests retry with exponential backoff on network errors and 502/503/504.
 * - Every request has a timeout; errors carry .status and .code from the server.
 */
const BASE = `${import.meta.env.VITE_API_URL || ""}/api`;
const DEFAULT_TIMEOUT = 30_000;
let csrfToken = null;

export const session = {
  setCsrf: (t) => { csrfToken = t || null; },
  getCsrf: () => csrfToken,
  clear: () => { csrfToken = null; },
};

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, network = false } = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.network = network;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const OFFLINE_MSG = "Can't reach the server. Check your connection; we'll keep trying.";

function headers(method, json = true, extra = {}) {
  const h = { ...extra };
  if (json) h["Content-Type"] = "application/json";
  if (UNSAFE.has(method) && csrfToken) h["X-CSRF-Token"] = csrfToken;
  return h;
}

function notifyExpired(res, body) {
  if (res.status === 401 && body?.code === "unauthenticated" && csrfToken) {
    csrfToken = null;
    window.dispatchEvent(new CustomEvent("auth:expired"));
  }
}

async function refreshCsrf() {
  try {
    const res = await fetch(`${BASE}/auth/me`, { credentials: "include" });
    if (res.ok) { csrfToken = (await res.json()).csrf; return true; }
  } catch { /* offline */ }
  return false;
}

async function errorFrom(res) {
  const body = await res.json().catch(() => ({}));
  notifyExpired(res, body);
  const fallback = res.status >= 500 ? "The server had a problem. Try again in a moment." : `Request failed (${res.status})`;
  return new ApiError(body.error || fallback, { status: res.status, code: body.code || null });
}

/** fetch with timeout + retries for idempotent requests. Returns the Response (ok or not). */
async function send(path, { method = "GET", body, json = true, timeout = DEFAULT_TIMEOUT, retries, signal, raw = false } = {}) {
  const attempts = (retries ?? (method === "GET" ? 2 : 0)) + 1;
  let csrfRetried = false;
  for (let i = 0; i < attempts; i += 1) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(new DOMException("timeout", "TimeoutError")), timeout);
    const onAbort = () => ctrl.abort(signal.reason);
    signal?.addEventListener("abort", onAbort);
    let res;
    try {
      res = await fetch(`${BASE}${path}`, {
        method, credentials: "include", body: json && body !== undefined ? JSON.stringify(body) : body,
        headers: headers(method, json && body !== undefined), signal: ctrl.signal,
      });
    } catch (e) {
      if (signal?.aborted) throw e;
      const timedOut = ctrl.signal.reason?.name === "TimeoutError";
      if (i < attempts - 1) { await sleep(400 * 2 ** i); continue; }
      throw new ApiError(timedOut ? "The server took too long to respond. Try again." : OFFLINE_MSG, { network: true });
    } finally {
      clearTimeout(timer); // the timeout covers getting a response, not reading a long stream
      // a streamed body is still being read after this returns: keep the caller's Stop button wired to it
      if (!raw) signal?.removeEventListener("abort", onAbort);
    }
    if (res.status === 403 && UNSAFE.has(method) && !csrfRetried) {
      const peek = await res.clone().json().catch(() => ({}));
      if (peek.code === "csrf" && await refreshCsrf()) { csrfRetried = true; i -= 1; continue; }
    }
    if ([502, 503, 504].includes(res.status) && i < attempts - 1) { await sleep(400 * 2 ** i); continue; }
    if (raw) return res;
    if (!res.ok) throw await errorFrom(res);
    return res;
  }
  throw new ApiError(OFFLINE_MSG, { network: true });
}

async function request(path, options = {}) {
  const res = await send(path, options);
  return res.status === 204 ? null : res.json();
}

/** Upload one file with progress reporting (XMLHttpRequest exposes upload progress; fetch does not). */
function upload(path, file, onProgress, { timeout = 180_000 } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);
    xhr.open("POST", `${BASE}${path}`);
    xhr.withCredentials = true;
    xhr.timeout = timeout;
    if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100)); };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText || "{}"); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else {
        notifyExpired({ status: xhr.status }, body);
        reject(new ApiError(body.error || (xhr.status === 413 ? "That file is too large." : `Upload failed (${xhr.status})`),
          { status: xhr.status, code: body.code }));
      }
    };
    xhr.onerror = () => reject(new ApiError(OFFLINE_MSG, { network: true }));
    xhr.ontimeout = () => reject(new ApiError("The upload took too long. Try a smaller file.", { network: true }));
    xhr.send(form);
  });
}

async function download(path, fallbackName) {
  const res = await send(path, { timeout: 60_000 });
  const disposition = res.headers.get("Content-Disposition") || "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] || fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const post = (path, body, opts) => request(path, { method: "POST", body, ...opts });
const put = (path, body) => request(path, { method: "PUT", body });
const patch = (path, body) => request(path, { method: "PATCH", body });
const del = (path, body) => request(path, { method: "DELETE", body });
const qs = (params) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")).toString();
  return s ? `?${s}` : "";
};

export const api = {
  health: () => request("/health", { retries: 1, timeout: 10_000 }),
  // conversations
  listConversations: ({ q, archived } = {}) => request(`/conversations${qs({ q, archived: archived ? 1 : undefined })}`),
  createConversation: () => post("/conversations"),
  getConversation: (id) => request(`/conversations/${id}`),
  updateConversation: (id, changes) => patch(`/conversations/${id}`, changes),
  deleteConversation: (id) => del(`/conversations/${id}`),
  clearConversation: (id) => del(`/conversations/${id}/messages`),
  exportConversation: (id, format) => download(`/conversations/${id}/export?format=${format}`, `conversation.${format}`),
  search: (q) => request(`/search${qs({ q })}`),
  // documents
  listDocuments: () => request("/documents"),
  documentsConfig: () => request("/documents/config"),
  deleteDocument: (id) => del(`/documents/${id}`),
  reindexDocument: (id) => post(`/documents/${id}/reindex`, undefined, { timeout: 120_000 }),
  uploadDocument: (file, onProgress) => upload("/documents", file, onProgress),
  // chat extras
  rate: (messageId, rating) => post(`/messages/${messageId}/feedback`, { rating }),
  tools: () => request("/tools"),
  uploadAttachment: (file, onProgress) => upload("/attachments", file, onProgress, { timeout: 60_000 }),
  deleteAttachment: (id) => del(`/attachments/${id}`),
  attachmentBlob: async (id) => (await send(`/attachments/${id}`)).blob(),
  downloadReport: (id, title) => download(`/reports/${id}`, `${(title || "report").replace(/[^\w\- ]+/g, "").trim() || "report"}.md`),
  handoff: (note, conversationId) => post("/handoffs", { note, conversation_id: conversationId || null }),
  myHandoffs: () => request("/handoffs"),
  // memory
  memory: () => request("/memory"),
  setMemoryEnabled: (enabled) => put("/memory/settings", { enabled }),
  addMemory: (text) => post("/memory", { text }),
  deleteMemory: (id) => del(`/memory/${id}`),
  clearMemory: () => del("/memory"),
  // auth
  authConfig: () => request("/auth/config", { retries: 1 }),
  me: () => request("/auth/me", { retries: 1 }),
  login: (email, password) => post("/auth/login", { email, password }),
  loginMfa: (mfaToken, code) => post("/auth/mfa", { mfa_token: mfaToken, code }),
  register: (name, email, password) => post("/auth/register", { name, email, password }),
  logout: () => post("/auth/logout"),
  logoutAll: () => post("/auth/logout-all"),
  forgot: (email) => post("/auth/forgot", { email }),
  reset: (token, password) => post("/auth/reset", { token, password }),
  verifyEmail: (token) => post("/auth/verify", { token }),
  resendVerification: (email) => post("/auth/resend-verification", { email }),
  // account
  settings: () => request("/settings"),
  saveSettings: (s) => put("/settings", s),
  updateProfile: (name) => patch("/account/profile", { name }),
  changePassword: (current, next) => post("/account/password", { current_password: current, new_password: next }),
  sessions: () => request("/account/sessions"),
  revokeSession: (id) => del(`/account/sessions/${id}`),
  sendMyVerification: () => post("/account/verify-email"),
  mfaSetup: (password) => post("/account/mfa/setup", { password }),
  mfaEnable: (code) => post("/account/mfa/enable", { code }),
  mfaDisable: (password, code) => post("/account/mfa/disable", { password, code }),
  exportAccount: () => download("/account/export", "nova-account-export.json"),
  deleteAccount: (password) => del("/account", { password, confirm: "DELETE" }),
  myUsage: () => request("/usage"),
  subjects: () => request("/subjects"),
  createSubject: (name, description) => post("/subjects", { name, description }),
  getSubject: (id) => request(`/subjects/${id}`),
  updateSubject: (id, changes) => patch(`/subjects/${id}`, changes),
  deleteSubject: (id) => del(`/subjects/${id}`),
  attachSubjectDoc: (id, documentId, role) => post(`/subjects/${id}/documents`, { document_id: documentId, role }),
  detachSubjectDoc: (id, documentId) => del(`/subjects/${id}/documents/${documentId}`),
  makePlan: (id, days, minutesPerDay, goal) => post(`/subjects/${id}/plan`, { days, minutes_per_day: minutesPerDay, goal }, { timeout: 120_000 }),
  setPlanDay: (id, day, done) => patch(`/subjects/${id}/plan/days/${day}`, { done }),
  studyNotes: (id) => request(`/subjects/${id}/notes`),
  makeNotes: (id, { topic, day }) => post(`/subjects/${id}/notes`, { topic, day }, { timeout: 120_000 }),
  getNote: (id, noteId) => request(`/subjects/${id}/notes/${noteId}`),
  deleteNote: (id, noteId) => del(`/subjects/${id}/notes/${noteId}`),
  startStudyChat: (id) => post(`/subjects/${id}/chat`),
  doubts: (params) => request(`/doubts${qs(params)}`),
  addDoubt: (id, question) => post(`/subjects/${id}/doubts`, { question }),
  updateDoubt: (id, changes) => patch(`/doubts/${id}`, changes),
  deleteDoubt: (id) => del(`/doubts/${id}`),
  channels: () => request("/channels"),
  channelLinkCode: (channel) => post(`/channels/${channel}/link-code`),
  unlinkChannel: (id) => del(`/channels/links/${id}`),
  // admin
  adminStats: () => request("/admin/stats"),
  adminUsage: (days) => request(`/admin/usage${qs({ days })}`),
  adminUsers: (params) => request(`/admin/users${qs(params)}`),
  adminUpdateUser: (id, changes) => patch(`/admin/users/${id}`, changes),
  adminRevokeSessions: (id) => post(`/admin/users/${id}/revoke-sessions`),
  adminHandoffs: (params) => request(`/admin/handoffs${qs(params)}`),
  adminResolveHandoff: (id, reply) => post(`/admin/handoffs/${id}/resolve`, { reply }),
  adminAudit: (params) => request(`/admin/audit${qs(params)}`),
  adminErrors: (params) => request(`/admin/errors${qs(params)}`),
  adminEvals: () => request("/admin/evals"),
  adminAddEvalCase: (c) => post("/admin/evals/cases", c),
  adminDeleteEvalCase: (id) => del(`/admin/evals/cases/${id}`),
  adminRunEval: (judge) => post("/admin/evals/run", { judge }),
  adminEvalRun: (id) => request(`/admin/evals/runs/${id}`),
  adminChannels: () => request("/admin/channels"),
};

/** Parse one SSE block ("data: {...}" lines; ":" comment lines are heartbeats). */
export function parseSSEBlock(raw) {
  const data = raw.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
  if (!data) return null;
  try { return JSON.parse(data); } catch { return null; }
}

/**
 * POST to an SSE endpoint and dispatch events: delta, step, done, error.
 * Errors before the stream starts carry preStream = true (nothing was stored on the server).
 * The stream is abandoned if the server goes silent for idleMs (heartbeats arrive every 15 s).
 */
export async function streamSSE(path, payload, { signal, onDelta, onStep, onDone, onUser, idleMs = 75_000 }) {
  const res = await send(path, { method: "POST", body: payload, raw: true, signal, timeout: 60_000 }).catch((e) => {
    if (e.name === "AbortError" || signal?.aborted) throw e;
    e.preStream = true;
    throw e;
  });
  if (!res.ok) {
    const err = await errorFrom(res);
    err.preStream = true;
    throw err;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  let idleTimer;
  const resetIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => reader.cancel("idle"), idleMs);
  };
  const handle = (raw) => {
    const evt = parseSSEBlock(raw);
    if (!evt) return;
    if (evt.type === "delta") onDelta(evt.text);
    else if (evt.type === "user") onUser?.(evt.message);
    else if (evt.type === "step") onStep?.(evt.step);
    else if (evt.type === "done") { finished = true; onDone(evt.message); }
    else if (evt.type === "error") {
      finished = true;
      const err = new ApiError(evt.message, { code: "stream_error" });
      err.saved = evt.saved || null;
      throw err;
    }
  };
  try {
    resetIdle();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      resetIdle();
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        handle(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
      }
    }
    if (buffer.trim()) handle(buffer);
  } finally {
    clearTimeout(idleTimer);
  }
  if (!finished) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const err = new ApiError("The connection dropped before the reply finished.", { network: true, code: "dropped" });
    err.dropped = true;
    throw err;
  }
}

export const streamChat = ({ conversationId, message, regenerate, editMessageId, docIds, useTools, agent, attachmentIds, ...cb }) =>
  streamSSE("/chat/stream", {
    conversation_id: conversationId, message, regenerate: !!regenerate, edit_message_id: editMessageId || undefined,
    doc_ids: docIds || [], tools: !!useTools, agent: agent || undefined, attachment_ids: attachmentIds || [],
  }, cb);

/** Continue an agent run after the user approved or declined a sensitive action. */
export const streamResume = ({ pendingId, approve, ...cb }) =>
  streamSSE("/chat/resume", { pending_id: pendingId, approve: !!approve }, cb);
