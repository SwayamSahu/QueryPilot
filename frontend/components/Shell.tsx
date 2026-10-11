"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, Activity, MessagesSquare, ShieldCheck, ChevronDown } from "lucide-react";
import clsx from "clsx";
import { useSession } from "./SessionProvider";

const NAV = [
  { href: "/", label: "Ask", icon: MessagesSquare },
  { href: "/catalog", label: "Metric catalog", icon: BookOpen },
  { href: "/ops", label: "Ops & evaluation", icon: Activity },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { session, users, switchUser, error } = useSession();
  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-[60px] shrink-0 flex-col items-center gap-2 border-r border-ink-700 bg-ink-900 py-3" aria-label="Main">
        <div className="mb-3 grid h-9 w-9 place-items-center rounded-xl bg-violet-500/90 text-white shadow-lg shadow-violet-900/40" title="QueryPilot">
          <ShieldCheck size={18} />
        </div>
        {NAV.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} title={label} aria-label={label}
            className={clsx("grid h-9 w-9 place-items-center rounded-lg transition",
              path === href ? "bg-violet-500/20 text-violet-300 ring-1 ring-violet-500/40" : "text-fog-400 hover:bg-ink-800 hover:text-fog-100")}>
            <Icon size={17} />
          </Link>
        ))}
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-ink-700 bg-ink-900/80 px-4 backdrop-blur">
          <div className="flex items-center gap-3">
            <span className="text-[15px] font-semibold tracking-tight">QueryPilot</span>
            <span className="hidden text-[12px] text-fog-400 sm:inline">Governed analytics copilot</span>
          </div>
          <div className="flex items-center gap-2">
            {session && (
              <span className="hidden items-center gap-1.5 text-[12px] text-fog-400 md:flex">
                <span className="h-1.5 w-1.5 rounded-full bg-good" /> {session.user.tenant}
              </span>
            )}
            <label className="relative">
              <span className="sr-only">Signed-in demo user</span>
              <select value={session?.user.user_id ?? ""} onChange={(e) => switchUser(e.target.value)}
                className="max-w-[58vw] truncate appearance-none rounded-lg border border-ink-600 bg-ink-800 py-1.5 pl-3 pr-8 text-[13px] sm:max-w-none text-fog-100 focus:border-violet-500 focus:outline-none">
                {users.map((u) => (
                  <option key={u.user_id} value={u.user_id}>{u.name} · {u.title} · {u.tenant}</option>
                ))}
              </select>
              <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-2.5 text-fog-400" />
            </label>
          </div>
        </header>
        {error && <div className="border-b border-bad/30 bg-bad/10 px-4 py-2 text-[13px] text-bad">{error}</div>}
        <main className="min-h-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
