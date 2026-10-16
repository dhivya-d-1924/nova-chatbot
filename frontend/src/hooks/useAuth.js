import { useCallback, useEffect, useState } from "react";
import { api, session } from "../lib/api";

/** Session state. The session itself lives in an HttpOnly cookie; we only keep the CSRF token in memory. */
export function useAuth() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState(null);

  const boot = useCallback(async () => {
    setLoading(true);
    setBootError(null);
    try {
      const { user: u, csrf } = await api.me();
      session.setCsrf(csrf);
      setUser(u);
    } catch (e) {
      if (e.status !== 401) setBootError(e.message); // offline or server down: show retry, not the sign-in form
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { boot(); }, [boot]);

  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  /** Accepts a sign-in response. Returns {mfa: token} or {verify: message} when another step is needed. */
  const accept = useCallback((res) => {
    if (res?.mfa_required) return { mfa: res.mfa_token };
    if (res?.verification_required) return { verify: res.message };
    if (res?.user && res?.csrf) {
      session.setCsrf(res.csrf);
      setUser(res.user);
      return { ok: true };
    }
    return { notice: res?.message };
  }, []);

  const login = useCallback(async (email, password) => accept(await api.login(email, password)), [accept]);
  const loginMfa = useCallback(async (token, code) => accept(await api.loginMfa(token, code)), [accept]);
  const register = useCallback(async (name, email, password) => accept(await api.register(name, email, password)), [accept]);
  const resetPassword = useCallback(async (token, password) => accept(await api.reset(token, password)), [accept]);
  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* the cookie is cleared server-side when reachable */ }
    session.clear();
    setUser(null);
  }, []);
  const updateUser = useCallback((u) => setUser((prev) => (prev ? { ...prev, ...u } : prev)), []);

  return { user, loading, bootError, boot, login, loginMfa, register, resetPassword, logout, updateUser };
}
