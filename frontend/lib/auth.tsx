"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, refreshSession, session } from "@/lib/api-client";
import type { AuthResponse, User } from "@/types/api";

type Status = "loading" | "authed" | "anon";

interface AuthContextValue {
  status: Status;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<{ status: Status; user: User | null }>({
    status: "loading",
    user: null,
  });

  const adopt = useCallback(
    (response: AuthResponse) => {
      session.set(response.access_token);
      // Another account's cached answers must never be shown to this one.
      queryClient.clear();
      setState({ status: "authed", user: response.user });
    },
    [queryClient],
  );

  const drop = useCallback(() => {
    session.set(null);
    queryClient.clear();
    setState({ status: "anon", user: null });
  }, [queryClient]);

  // On load there is no access token (it lives in memory), so the refresh cookie is what
  // says whether someone is still signed in.
  useEffect(() => {
    let active = true;
    refreshSession().then((response) => {
      if (!active) return;
      if (response) adopt(response);
      else setState({ status: "anon", user: null });
    });
    session.onLost(drop);
    return () => {
      active = false;
      session.onLost(null);
    };
  }, [adopt, drop]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login: async (email, password) => adopt(await api.login(email, password)),
      register: async (email, password, fullName) =>
        adopt(await api.register(email, password, fullName)),
      logout: async () => {
        try {
          await api.logout();
        } finally {
          drop();
        }
      },
    }),
    [state, adopt, drop],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

/** Only ever follow a same-site path: `?next=//evil.example` must not become a redirect. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/analyze";
}
