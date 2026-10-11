"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, loadSession, saveSession, type Session } from "@/lib/api";
import type { DemoUser } from "@/lib/types";

interface Ctx { session: Session | null; users: DemoUser[]; switchUser: (id: string) => Promise<void>; error: string | null }
const SessionCtx = createContext<Ctx>({ session: null, users: [], switchUser: async () => {}, error: null });

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [error, setError] = useState<string | null>(null);

  const switchUser = useCallback(async (id: string) => {
    try {
      const r = await api.login(id);
      const s = { token: r.token, user: { ...r.user, user_id: id } as DemoUser };
      saveSession(s);
      setSession(s);
      setError(null);
    } catch (e) {
      setError(`Can't reach the QueryPilot API (${(e as Error).message}). Is the backend running?`);
    }
  }, []);

  useEffect(() => {
    api.demoUsers().then(setUsers).catch((e) => setError(`Can't reach the QueryPilot API (${e.message}).`));
    const s = loadSession();
    if (s) setSession(s);
    else switchUser("maya");
  }, [switchUser]);

  return <SessionCtx.Provider value={{ session, users, switchUser, error }}>{children}</SessionCtx.Provider>;
}

export const useSession = () => useContext(SessionCtx);
