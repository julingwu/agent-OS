import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import api, { auth } from "../api.js";

const AuthContext = createContext({
  me: null,
  token: null,
  tenantId: "",
  entityId: "",
  sessionEpoch: 0,
  isLoggedIn: false,
  loading: true,
  login: async () => {},
  logout: async () => {},
  refreshSession: async () => {},
});

export function AuthProvider({ children }) {
  const [session, setSession] = useState({ me: null, epoch: 0, loading: true });

  const refreshSession = useCallback(async () => {
    try {
      const data = await api.me();
      setSession({ me: data, epoch: auth.epoch(), loading: false });
      return data;
    } catch {
      setSession({ me: null, epoch: auth.epoch(), loading: false });
      return null;
    }
  }, []);

  useEffect(() => {
    refreshSession();
  }, [refreshSession]);

  const login = useCallback(async (identityId, secret) => {
    const res = await api.login(identityId, secret);
    await refreshSession();
    return res;
  }, [refreshSession]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      auth.clear();
      setSession({ me: null, epoch: auth.epoch(), loading: false });
    }
  }, []);

  const value = {
    me: session.me,
    token: auth.token(),
    tenantId: session.me?.tenant_id || "LE-GROUP-HQ",
    entityId: session.me?.entity_id || "",
    sessionEpoch: session.epoch,
    isLoggedIn: Boolean(session.me),
    loading: session.loading,
    login,
    logout,
    refreshSession,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

export default AuthContext;
