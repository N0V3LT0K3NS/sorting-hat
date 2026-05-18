"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import styles from "./dev.module.css";

/**
 * /dev — the developer / diagnostic view.
 *
 * A separate route, deliberately away from the visitor kiosk flow at `/`. It
 * shows, live as an interview runs, what the background classifier is
 * capturing: the four signal weights as moving bars, base-question progress,
 * the interview phase, and the leading/chosen template.
 *
 * It polls the delivery server's `GET /live/<session-id>` every ~1.75 s — the
 * same polling shape the Complete screen uses for `/status`. A pending
 * default or an unreachable server degrades to a calm line; polling continues.
 *
 * This is a tool, not a visitor surface: clarity over polish. It does not
 * touch, and is not reachable from, the kiosk flow.
 */

/** The four classifier signals, in display order. */
const SIGNAL_KEYS = ["iceberg", "two_buttons", "compass", "arc"] as const;
type SignalKey = (typeof SIGNAL_KEYS)[number];

/** Interview phases the `/live` endpoint may report. */
type Phase = "pending" | "base_questions" | "probing" | "complete";

/** The `/live/<session-id>` JSON shape (see delivery/server.py). */
interface LiveState {
  session_id: string;
  phase: Phase;
  base_questions_completed: number;
  base_questions_total: number;
  signals: Record<SignalKey, number>;
  leading_template: string | null;
  chosen_template: string | null;
  routing_done: boolean;
  turn_count: number;
  updated_at: string | null;
}

/** How often to poll `/live` while a session is being watched. */
const POLL_INTERVAL_MS = 1_750;

/** Human-readable label for each signal key. */
const SIGNAL_LABEL: Record<SignalKey, string> = {
  iceberg: "iceberg",
  two_buttons: "two_buttons",
  compass: "compass",
  arc: "arc",
};

/** Human-readable label for each phase. */
const PHASE_LABEL: Record<Phase, string> = {
  pending: "pending — no state yet",
  base_questions: "base questions",
  probing: "probing",
  complete: "complete",
};

function DevView() {
  const searchParams = useSearchParams();

  const baseUrl = useMemo(
    () =>
      (
        process.env.NEXT_PUBLIC_DELIVERY_SERVER_URL ?? "http://localhost:8808"
      ).replace(/\/+$/, ""),
    [],
  );

  // The session id being watched. Seeded from the `?session=` query param;
  // editable via the text input so a developer can repoint it live.
  const [sessionId, setSessionId] = useState<string>("");
  const [draft, setDraft] = useState<string>("");

  // Seed from the query param once, on first mount / param change.
  useEffect(() => {
    const fromQuery = (searchParams.get("session") ?? "").trim();
    if (fromQuery) {
      setSessionId(fromQuery);
      setDraft(fromQuery);
    }
  }, [searchParams]);

  // The latest live state, and whether the last poll could reach the server.
  const [live, setLive] = useState<LiveState | null>(null);
  const [reachable, setReachable] = useState(true);
  // Wall-clock time of the last successful poll — so a developer can see the
  // view itself is alive even when the interview state is not changing.
  const [lastPollAt, setLastPollAt] = useState<string | null>(null);

  // Poll `/live/<session-id>` on an interval, mirroring CompleteScreen's
  // poll loop: a self-rescheduling async fn, cleaned up on unmount / id change.
  useEffect(() => {
    if (!sessionId) {
      setLive(null);
      return;
    }

    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      if (stopped) return;
      try {
        const res = await fetch(`${baseUrl}/live/${sessionId}`, {
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = (await res.json()) as LiveState;
        if (stopped) return;
        setLive(data);
        setReachable(true);
        setLastPollAt(new Date().toLocaleTimeString());
      } catch {
        // A single failed poll is not fatal — the delivery server may be
        // starting, or wifi hiccuped. Note it calmly and keep polling.
        if (!stopped) setReachable(false);
      }
      if (!stopped) timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId, baseUrl]);

  const applyDraft = useCallback(() => {
    const next = draft.trim();
    if (next) setSessionId(next);
  }, [draft]);

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <div className={styles.header}>
          <span className={styles.title}>sorting-hat · classifier dev view</span>
          <span className={styles.subtitle}>
            Diagnostic only — live view of the background classifier. Not part
            of the visitor kiosk flow.
          </span>
        </div>

        <div className={styles.sessionRow}>
          <input
            className={styles.sessionInput}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyDraft();
            }}
            placeholder="session id (e.g. interview-a1b2c3)"
            spellCheck={false}
            autoComplete="off"
          />
          <button
            className={styles.sessionButton}
            onClick={applyDraft}
            disabled={!draft.trim() || draft.trim() === sessionId}
          >
            Watch
          </button>
        </div>

        {!sessionId ? (
          <p className={styles.empty}>
            Enter a session id above (or open this page with{" "}
            <code>?session=&lt;id&gt;</code>) to watch a running interview.
          </p>
        ) : (
          <LivePanel
            sessionId={sessionId}
            live={live}
            reachable={reachable}
            lastPollAt={lastPollAt}
          />
        )}
      </div>
    </div>
  );
}

/** The live readout for a watched session — bars, progress, facts, status. */
function LivePanel({
  sessionId,
  live,
  reachable,
  lastPollAt,
}: {
  sessionId: string;
  live: LiveState | null;
  reachable: boolean;
  lastPollAt: string | null;
}) {
  // Before the first poll lands, show a calm waiting line.
  if (!live) {
    return (
      <p className={styles.empty}>
        Watching <code>{sessionId}</code> — waiting for the first response…
      </p>
    );
  }

  const signals = live.signals ?? {
    iceberg: 0,
    two_buttons: 0,
    compass: 0,
    arc: 0,
  };

  // Scale bar fills against the largest signal so movement stays visible even
  // when weights are small; never divide by zero.
  const maxSignal = Math.max(
    ...SIGNAL_KEYS.map((k) => Number(signals[k]) || 0),
    0.0001,
  );

  // Which bar to emphasise. The leading template names the signal family it
  // came from, so a direct key match highlights that bar. If the name does
  // not match a signal key (or there is none yet), fall back to the strongest
  // signal — there is always a visible leader once turns are classified.
  const leadingByName = SIGNAL_KEYS.find(
    (k) => k === live.leading_template,
  );
  const strongest = SIGNAL_KEYS.reduce((best, k) =>
    (Number(signals[k]) || 0) > (Number(signals[best]) || 0) ? k : best,
  );
  const leadingKey: SignalKey | null =
    leadingByName ?? (maxSignal > 0.0001 ? strongest : null);

  const total = Math.max(live.base_questions_total, 0);
  const done = Math.max(live.base_questions_completed, 0);
  const progressPct = total > 0 ? Math.min((done / total) * 100, 100) : 0;

  return (
    <>
      {/* --- The four signal weights as horizontal bars --- */}
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Classifier signal weights</span>
        <div className={styles.bars}>
          {SIGNAL_KEYS.map((key) => {
            const value = Number(signals[key]) || 0;
            const fillPct = Math.min((value / maxSignal) * 100, 100);
            const isLeading = leadingKey === key;
            return (
              <div
                key={key}
                className={styles.bar}
                data-leading={isLeading}
              >
                <span className={styles.barName}>{SIGNAL_LABEL[key]}</span>
                <div className={styles.barTrack}>
                  <div
                    className={styles.barFill}
                    style={{ width: `${fillPct}%` }}
                  />
                </div>
                <span className={styles.barValue}>{value.toFixed(3)}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* --- Base-question progress --- */}
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Base-question progress</span>
        <div className={styles.progressTrack}>
          <div
            className={styles.progressFill}
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <span className={styles.progressLabel}>
          base questions: {done} / {total || "—"}
        </span>
      </div>

      {/* --- Phase, templates, counts --- */}
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Interview state</span>
        <div className={styles.facts}>
          <Fact label="phase" value={PHASE_LABEL[live.phase] ?? live.phase} />
          <Fact
            label="leading template"
            value={live.leading_template ?? "—"}
            dim={!live.leading_template}
          />
          <Fact
            label="chosen template"
            value={live.chosen_template ?? "—"}
            accent={!!live.chosen_template}
            dim={!live.chosen_template}
          />
          <Fact
            label="routing done"
            value={live.routing_done ? "yes" : "no"}
            accent={live.routing_done}
          />
          <Fact label="turn count" value={String(live.turn_count)} />
          <Fact
            label="updated at"
            value={live.updated_at ?? "—"}
            dim={!live.updated_at}
          />
        </div>
      </div>

      {/* --- Footer: liveness of the view itself --- */}
      <p className={styles.status}>
        {reachable ? (
          <>
            polling <code>{sessionId}</code> every{" "}
            {(POLL_INTERVAL_MS / 1000).toFixed(2)}s
            {lastPollAt ? ` · last refresh ${lastPollAt}` : ""}
          </>
        ) : (
          <span className={styles.statusWarn}>
            delivery server unreachable — retrying every{" "}
            {(POLL_INTERVAL_MS / 1000).toFixed(2)}s
          </span>
        )}
      </p>
    </>
  );
}

/** One label/value pair in the interview-state facts grid. */
function Fact({
  label,
  value,
  accent,
  dim,
}: {
  label: string;
  value: string;
  accent?: boolean;
  dim?: boolean;
}) {
  return (
    <div className={styles.fact}>
      <span className={styles.factKey}>{label}</span>
      <span
        className={styles.factValue}
        data-accent={accent || undefined}
        data-dim={dim || undefined}
      >
        {value}
      </span>
    </div>
  );
}

/**
 * `useSearchParams` requires a Suspense boundary in the App Router, so the
 * actual view is wrapped here. The fallback is brief and calm.
 */
export default function DevPage() {
  return (
    <Suspense
      fallback={
        <div className={styles.page}>
          <div className={styles.inner}>
            <p className={styles.empty}>Loading…</p>
          </div>
        </div>
      }
    >
      <DevView />
    </Suspense>
  );
}
