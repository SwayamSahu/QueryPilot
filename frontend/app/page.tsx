"use client";
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { ArrowUp, Loader2, Plus, Sparkles, User } from "lucide-react";
import { api } from "@/lib/api";
import type { AskResponse } from "@/lib/types";
import { useSession } from "@/components/SessionProvider";
import { AnswerView, StatusBadge } from "@/components/AnswerView";

const EXAMPLES = [
  "Which regions had declining gross margins last quarter, and what changed compared with the previous quarter?",
  "Revenue by product category in 2025",
  "Monthly revenue trend for the last 12 months",
  "Top 5 products by gross profit last quarter",
  "Revenue by channel last quarter vs same period last year",
  "Show me customer emails",
];

interface Turn { q: string; r?: AskResponse; error?: string }

export default function AskPage() {
  const { session } = useSession();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [conv, setConv] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setTurns([]); setConv(null); setSelected(null); }, [session?.user.user_id]);
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" }); }, [turns]);

  async function ask(q: string) {
    if (!session || !q.trim() || busy) return;
    const idx = turns.length;
    setTurns((t) => [...t, { q }]);
    setSelected(idx);
    setInput("");
    setBusy(true);
    try {
      const r = await api.ask(session.token, q, conv);
      setConv(r.conversation_id);
      setTurns((t) => t.map((x, i) => (i === idx ? { ...x, r } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === idx ? { ...x, error: (e as Error).message } : x)));
    } finally {
      setBusy(false);
    }
  }

  const current = selected !== null ? turns[selected] : null;

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[1fr_380px]">
      {/* ------------------------------------------------ canvas */}
      <div className="scroll-thin min-h-0 overflow-y-auto p-5">
        {!current && (
          <div className="mx-auto mt-10 max-w-2xl text-center">
            <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-violet-500/15 text-violet-300 ring-1 ring-violet-500/30"><Sparkles size={22} /></div>
            <h1 className="text-[22px] font-semibold tracking-tight">Ask your data a question</h1>
            <p className="mx-auto mt-2 max-w-lg text-[14px] text-fog-400">
              Answers come from governed metric definitions, run as read-only SQL scoped to your role, and arrive with the SQL, checks and trace as evidence.
            </p>
            <div className="mt-6 grid gap-2 text-left sm:grid-cols-2">
              {EXAMPLES.map((e) => (
                <button key={e} onClick={() => ask(e)} disabled={!session || busy}
                  className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-[13px] text-fog-300 transition hover:border-violet-500/50 hover:text-fog-100">
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}
        {current && !current.r && !current.error && (
          <div className="card flex items-center gap-3 p-5 text-[14px] text-fog-300"><Loader2 size={16} className="animate-spin text-violet-400" />
            Resolving intent, retrieving metrics, validating SQL…</div>
        )}
        {current?.error && <div className="card border-bad/40 p-5 text-[14px] text-bad">{current.error}</div>}
        {current?.r && <AnswerView key={current.r.trace_id} r={current.r} />}
      </div>

      {/* ------------------------------------------------ assistant panel */}
      <aside className="flex min-h-0 flex-col border-l border-ink-700 bg-ink-900">
        <div className="flex items-center justify-between border-b border-ink-700 px-4 py-2.5">
          <span className="flex items-center gap-2 text-[13px] font-medium"><Sparkles size={14} className="text-violet-400" />AI Assistant</span>
          <button className="btn !px-2 !py-1 text-[12px]" onClick={() => { setTurns([]); setConv(null); setSelected(null); }} title="New conversation">
            <Plus size={13} /> New
          </button>
        </div>
        <div ref={listRef} className="scroll-thin min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {session && (
            <p className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-[12px] text-fog-400">
              Signed in as <b className="text-fog-300">{session.user.name}</b> ({session.user.role.replace("_", " ")}
              {session.user.regions?.length ? `, ${session.user.regions.join(", ")} only` : ""}) · {session.user.tenant}. Follow-ups like
              “what about by category?” build on the previous answer.
            </p>
          )}
          {turns.map((t, i) => (
            <div key={i} className="space-y-2">
              <div className="flex items-start gap-2.5">
                <div className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-ink-700 text-fog-300"><User size={14} /></div>
                <p className="pt-1 text-[13px] text-fog-100">{t.q}</p>
              </div>
              <button onClick={() => setSelected(i)}
                className={clsx("flex w-full items-start gap-2.5 rounded-lg p-1.5 text-left transition", selected === i ? "bg-violet-500/10 ring-1 ring-violet-500/30" : "hover:bg-ink-850")}>
                <div className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-violet-500/90 text-white"><Sparkles size={13} /></div>
                <div className="min-w-0 pt-0.5">
                  {!t.r && !t.error && <span className="flex items-center gap-1.5 text-[12px] text-fog-400"><Loader2 size={12} className="animate-spin" />Working…</span>}
                  {t.error && <span className="text-[12px] text-bad">{t.error}</span>}
                  {t.r && (
                    <>
                      <div className="mb-1 flex items-center gap-1.5"><StatusBadge r={t.r} />
                        <span className="text-[11px] text-fog-500">{t.r.spans.length} steps · {t.r.timings.total_ms?.toFixed(0)} ms</span></div>
                      <p className="line-clamp-4 text-[12.5px] leading-relaxed text-fog-300">{t.r.answer}</p>
                      {t.r.suggestions.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {t.r.suggestions.map((s) => (
                            <span key={s} role="button" tabIndex={0} onClick={(e) => { e.stopPropagation(); ask(s); }}
                              onKeyDown={(e) => { if (e.key === "Enter") ask(s); }}
                              className="cursor-pointer rounded-md border border-ink-600 px-2 py-0.5 text-[11px] text-fog-300 hover:border-violet-500/60">{s}</span>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </button>
            </div>
          ))}
        </div>
        <form className="border-t border-ink-700 p-3" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
          <div className="flex items-end gap-2 rounded-xl border border-ink-600 bg-ink-850 p-2 focus-within:border-violet-500/70">
            <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={2} maxLength={2000}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input); } }}
              placeholder="Ask about revenue, margins, orders…" aria-label="Question"
              className="min-h-[40px] flex-1 resize-none bg-transparent px-1 text-[13px] text-fog-100 placeholder:text-fog-500 focus:outline-none" />
            <button type="submit" className="btn-primary !p-2" disabled={busy || !input.trim() || !session} aria-label="Send">
              {busy ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} />}
            </button>
          </div>
        </form>
      </aside>
    </div>
  );
}
