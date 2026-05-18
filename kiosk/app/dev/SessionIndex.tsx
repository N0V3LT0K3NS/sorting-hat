"use client";

import { useEffect, useState } from "react";
import styles from "./dev.module.css";
import {
  SessionSummary,
  deriveStatus,
  relativeTime,
  STATUS_LABEL,
  STATUS_TONE,
} from "./types";

/** How often to re-fetch `/sessions` so new interviews appear live. */
const POLL_INTERVAL_MS = 4_000;

/**
 * The dashboard INDEX — a live list of every interview the machine has run.
 *
 * Polls `GET /sessions` every ~4 s; rows are most-recent first (the server
 * already sorts them). Clicking a row opens its detail view via `onOpen`,
 * which the page routes to `/dev?session=<id>`.
 */
export function SessionIndex({
  baseUrl,
  onOpen,
}: {
  baseUrl: string;
  onOpen: (sessionId: string) => void;
}) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [reachable, setReachable] = useState(true);
  const [lastPollAt, setLastPollAt] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      if (stopped) return;
      try {
        const res = await fetch(`${baseUrl}/sessions`, { cache: "no-store" });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = (await res.json()) as { sessions?: SessionSummary[] };
        if (stopped) return;
        setSessions(Array.isArray(data.sessions) ? data.sessions : []);
        setReachable(true);
        setLastPollAt(new Date().toLocaleTimeString());
      } catch {
        // A single failed poll is not fatal — the delivery server may be
        // starting. Note it calmly and keep polling.
        if (!stopped) setReachable(false);
      }
      if (!stopped) timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [baseUrl]);

  // Before the first poll lands.
  if (sessions === null) {
    return (
      <p className={styles.empty}>
        {reachable
          ? "Loading sessions…"
          : "Delivery server unreachable — retrying…"}
      </p>
    );
  }

  return (
    <>
      <div className={styles.section}>
        <span className={styles.sectionLabel}>
          Interviews ({sessions.length})
        </span>

        {sessions.length === 0 ? (
          <p className={styles.empty}>
            No interviews yet. When a visitor starts a session it appears here.
          </p>
        ) : (
          <div className={styles.list}>
            {sessions.map((s) => (
              <SessionRow
                key={s.session_id}
                summary={s}
                baseUrl={baseUrl}
                onOpen={onOpen}
              />
            ))}
          </div>
        )}
      </div>

      <p className={styles.status}>
        {reachable ? (
          <>
            polling <code>/sessions</code> every{" "}
            {(POLL_INTERVAL_MS / 1000).toFixed(0)}s
            {lastPollAt ? ` · last refresh ${lastPollAt}` : ""}
          </>
        ) : (
          <span className={styles.statusWarn}>
            delivery server unreachable — retrying every{" "}
            {(POLL_INTERVAL_MS / 1000).toFixed(0)}s
          </span>
        )}
      </p>
    </>
  );
}

/** One interview in the index list — a clickable row. */
function SessionRow({
  summary,
  baseUrl,
  onOpen,
}: {
  summary: SessionSummary;
  baseUrl: string;
  onOpen: (sessionId: string) => void;
}) {
  const status = deriveStatus(summary);

  const metaBits: string[] = [`${summary.turn_count} turns`];
  if (summary.chosen_template) metaBits.push(`→ ${summary.chosen_template}`);

  return (
    <button
      className={styles.row}
      onClick={() => onOpen(summary.session_id)}
      type="button"
    >
      {summary.has_portrait && summary.portrait_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className={styles.thumb}
          src={`${baseUrl}${summary.portrait_url}`}
          alt=""
        />
      ) : (
        <div className={styles.thumbPlaceholder} />
      )}

      <div className={styles.rowMain}>
        <span className={styles.rowId}>{summary.session_id}</span>
        <span className={styles.rowMeta}>
          {metaBits.join("  ·  ")}
        </span>
      </div>

      <div className={styles.rowRight}>
        <span className={styles.badge} data-tone={STATUS_TONE[status]}>
          {STATUS_LABEL[status]}
        </span>
        <span className={styles.rowMeta}>
          {relativeTime(summary.updated_at)}
        </span>
      </div>
    </button>
  );
}
