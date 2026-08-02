/** Who am I this session (P3-05 roles). Populated by the AuthGate from
 * POST /api/session's response ({identity, role}) — the only place the
 * backend states the role. Deliberately NOT persisted: a downgrade must
 * land on the next session establish, never be papered over by cache.
 *
 * `role` null = the backend predates roles or auth is open — treated as
 * operator (the server would still 403 a real viewer's mutations; the
 * client gate is UX, the middleware is the enforcement). */
import { create } from "zustand";

export type SessionRole = "viewer" | "operator";

interface SessionState {
  role: SessionRole | null;
  identity: string | null;
  setSession: (info: { role?: SessionRole | null; identity?: string | null }) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  role: null,
  identity: null,
  setSession: (info) =>
    set({ role: info.role ?? null, identity: info.identity ?? null }),
}));

/** Viewer sessions render read-only surfaces — mutations would 403 at the
 * middleware anyway; hiding the buttons keeps the UI honest about it. */
export const useIsOperator = () =>
  useSessionStore((s) => s.role !== "viewer");
