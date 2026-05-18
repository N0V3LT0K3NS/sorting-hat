"use client";

import { useEffect, useState } from "react";
import styles from "./dev.module.css";
import { Fact } from "./Fact";
import { SignalBars } from "./SignalBars";
import {
  Classification,
  LiveState,
  PipelineStatus,
  PHASE_LABEL,
  SessionSummary,
  STAGE_LABEL,
  STAGE_ORDER,
  Stage,
  TranscriptTurn,
  deriveStatus,
} from "./types";

/** Poll cadences — live state moves fast, the offline pipeline slower. */
const LIVE_POLL_MS = 2_000;
const STATUS_POLL_MS = 3_000;
/** While an interview is active its index summary is re-checked occasionally
 *  so the detail view can flip to the result panel once the pipeline starts. */
const SUMMARY_POLL_MS = 5_000;

/**
 * The dashboard DETAIL view for one session.
 *
 * It first fetches the session's index summary to decide which sub-panel to
 * show, then re-polls it so a session that finishes mid-view flips panels:
 *
 *   - ACTIVE  (phase base_questions / probing) -> the live classifier view,
 *     the four moving signal bars + base-question progress, polling /live.
 *   - PIPELINE (interview done, offline pipeline running) -> the stage row,
 *     polling /status, revealing the result once `done`.
 *   - COMPLETE / IDLE -> the full local record: portrait, classification,
 *     filled result, and the full transcript.
 *
 * Every fetch degrades gracefully — a pending or unreachable server, or a
 * partial session, shows what exists and keeps a calm line.
 */
export function SessionDetail({
  sessionId,
  baseUrl,
  onBack,
}: {
  sessionId: string;
  baseUrl: string;
  onBack: () => void;
}) {
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [reachable, setReachable] = useState(true);
  const [loaded, setLoaded] = useState(false);

  // Poll the session's index summary — this is what routes the panels below.
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
        const found =
          (data.sessions ?? []).find((s) => s.session_id === sessionId) ??
          null;
        setSummary(found);
        setReachable(true);
        setLoaded(true);
      } catch {
        if (!stopped) {
          setReachable(false);
          setLoaded(true);
        }
      }
      if (!stopped) timer = setTimeout(poll, SUMMARY_POLL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId, baseUrl]);

  const status = summary ? deriveStatus(summary) : null;

  return (
    <>
      <button className={styles.backLink} onClick={onBack} type="button">
        ← all interviews
      </button>

      <div className={styles.header}>
        <span className={styles.title}>{sessionId}</span>
        <span className={styles.subtitle}>
          {!loaded
            ? "Loading session…"
            : !reachable
              ? "Delivery server unreachable — retrying."
              : !summary
                ? "Session not found in the index — it may have been removed."
                : `Phase: ${PHASE_LABEL[summary.phase]}`}
        </span>
      </div>

      {!loaded ? (
        <p className={styles.empty}>Loading…</p>
      ) : !summary ? (
        <p className={styles.empty}>
          No session <code>{sessionId}</code> on this machine.
        </p>
      ) : status === "active" ? (
        <LivePanel sessionId={sessionId} baseUrl={baseUrl} />
      ) : status === "pipeline" ? (
        <PipelinePanel sessionId={sessionId} baseUrl={baseUrl} />
      ) : (
        // complete / error / idle — show whatever record exists.
        <RecordPanel
          sessionId={sessionId}
          baseUrl={baseUrl}
          summary={summary}
        />
      )}
    </>
  );
}

/* --- ACTIVE: the live classifier view ------------------------------------ */

/** The live readout for a running interview — the preserved /dev today. */
function LivePanel({
  sessionId,
  baseUrl,
}: {
  sessionId: string;
  baseUrl: string;
}) {
  const [live, setLive] = useState<LiveState | null>(null);
  const [reachable, setReachable] = useState(true);
  const [lastPollAt, setLastPollAt] = useState<string | null>(null);

  useEffect(() => {
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
        if (!stopped) setReachable(false);
      }
      if (!stopped) timer = setTimeout(poll, LIVE_POLL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId, baseUrl]);

  if (!live) {
    return (
      <p className={styles.empty}>
        Watching <code>{sessionId}</code> — waiting for the first response…
      </p>
    );
  }

  const total = Math.max(live.base_questions_total, 0);
  const done = Math.max(live.base_questions_completed, 0);
  const progressPct = total > 0 ? Math.min((done / total) * 100, 100) : 0;

  return (
    <>
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Classifier signal weights</span>
        <SignalBars
          signals={live.signals}
          leadingTemplate={live.leading_template}
        />
      </div>

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

      <p className={styles.status}>
        {reachable ? (
          <>
            polling <code>/live</code> every{" "}
            {(LIVE_POLL_MS / 1000).toFixed(0)}s
            {lastPollAt ? ` · last refresh ${lastPollAt}` : ""}
          </>
        ) : (
          <span className={styles.statusWarn}>
            delivery server unreachable — retrying every{" "}
            {(LIVE_POLL_MS / 1000).toFixed(0)}s
          </span>
        )}
      </p>
    </>
  );
}

/* --- PIPELINE: the offline portrait pipeline running --------------------- */

/** The mid-pipeline stage row — reveals the record once the pipeline is done. */
function PipelinePanel({
  sessionId,
  baseUrl,
}: {
  sessionId: string;
  baseUrl: string;
}) {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [reachable, setReachable] = useState(true);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      if (stopped) return;
      try {
        const res = await fetch(`${baseUrl}/status/${sessionId}`, {
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = (await res.json()) as PipelineStatus;
        if (stopped) return;
        setStatus(data);
        setReachable(true);
      } catch {
        if (!stopped) setReachable(false);
      }
      if (!stopped) timer = setTimeout(poll, STATUS_POLL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId, baseUrl]);

  // Once the pipeline is done, fall through to the full record browser.
  if (status?.stage === "done") {
    return <RecordPanel sessionId={sessionId} baseUrl={baseUrl} />;
  }

  return (
    <>
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Portrait pipeline</span>
        <div className={styles.stageRow}>
          {STAGE_ORDER.map((s) => (
            <span
              key={s}
              className={styles.stageChip}
              data-state={stageState(status?.stage ?? "pending", s)}
            >
              {STAGE_LABEL[s]}
            </span>
          ))}
        </div>
        {status?.stage === "error" && status.error ? (
          <p className={styles.statusWarn}>pipeline error: {status.error}</p>
        ) : null}
      </div>

      <p className={styles.status}>
        {reachable ? (
          <>
            interview done — pipeline running. polling <code>/status</code>{" "}
            every {(STATUS_POLL_MS / 1000).toFixed(0)}s
          </>
        ) : (
          <span className={styles.statusWarn}>
            delivery server unreachable — retrying
          </span>
        )}
      </p>
    </>
  );
}

/** Where stage `s` sits relative to the current pipeline stage. */
function stageState(
  current: Stage,
  s: Stage,
): "done" | "current" | "pending" {
  const order: Stage[] = STAGE_ORDER;
  const ci = order.indexOf(current);
  const si = order.indexOf(s);
  if (current === "done") return "done";
  if (ci < 0) return "pending"; // current is 'pending' / 'error'
  if (si < ci) return "done";
  if (si === ci) return "current";
  return "pending";
}

/* --- COMPLETE / IDLE: the full local record ------------------------------ */

/**
 * The full local record browser — portrait, classification, filled result,
 * and the full transcript. Each artifact is fetched independently and
 * degrades to a calm line if absent, so a partial session shows what exists.
 */
function RecordPanel({
  sessionId,
  baseUrl,
  summary,
}: {
  sessionId: string;
  baseUrl: string;
  summary?: SessionSummary;
}) {
  const classification = useJsonArtifact<Classification>(
    baseUrl,
    sessionId,
    "classification.json",
  );
  const result = useJsonArtifact<Record<string, unknown>>(
    baseUrl,
    sessionId,
    "result.json",
  );
  const transcript = useJsonArtifact<TranscriptTurn[]>(
    baseUrl,
    sessionId,
    "transcript.json",
  );

  const portraitUrl = summary?.has_portrait
    ? `${baseUrl}/${sessionId}/portrait.png`
    : null;

  return (
    <>
      {/* --- Portrait --- */}
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Portrait</span>
        {portraitUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            className={styles.portrait}
            src={portraitUrl}
            alt={`portrait for ${sessionId}`}
          />
        ) : (
          <p className={styles.empty}>No portrait for this session.</p>
        )}
      </div>

      {/* --- Classification --- */}
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Classification</span>
        {classification.state === "loading" ? (
          <p className={styles.empty}>Loading classification…</p>
        ) : classification.data ? (
          <div className={styles.classBlock}>
            <div className={styles.facts}>
              <Fact
                label="template"
                value={classification.data.template ?? "—"}
                accent={!!classification.data.template}
              />
              <Fact
                label="confidence"
                value={
                  typeof classification.data.confidence === "number"
                    ? classification.data.confidence.toFixed(2)
                    : "—"
                }
              />
            </div>
            {classification.data.reasoning ? (
              <p className={styles.reasoning}>
                {classification.data.reasoning}
              </p>
            ) : null}
          </div>
        ) : (
          <p className={styles.empty}>No classification recorded.</p>
        )}
      </div>

      {/* --- Filled result --- */}
      <div className={styles.section}>
        <span className={styles.sectionLabel}>Result</span>
        {result.state === "loading" ? (
          <p className={styles.empty}>Loading result…</p>
        ) : result.data ? (
          <div className={styles.facts}>
            {Object.entries(result.data).map(([key, value]) => (
              <Fact
                key={key}
                label={key}
                value={
                  typeof value === "string"
                    ? value
                    : JSON.stringify(value)
                }
              />
            ))}
          </div>
        ) : (
          <p className={styles.empty}>No result recorded.</p>
        )}
      </div>

      {/* --- Full transcript --- */}
      <div className={styles.section}>
        <span className={styles.sectionLabel}>
          Transcript
          {transcript.data ? ` (${transcript.data.length} turns)` : ""}
        </span>
        {transcript.state === "loading" ? (
          <p className={styles.empty}>Loading transcript…</p>
        ) : transcript.data && transcript.data.length > 0 ? (
          <div className={styles.transcript}>
            {transcript.data.map((turn, i) => {
              const speaker = turn.speaker === "interviewer"
                ? "interviewer"
                : turn.speaker === "interviewee"
                  ? "interviewee"
                  : turn.speaker || "unknown";
              return (
                <div key={i} className={styles.turn} data-speaker={speaker}>
                  <span className={styles.turnSpeaker}>{speaker}</span>
                  <span className={styles.turnText}>
                    {turn.text ?? ""}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <p className={styles.empty}>No transcript recorded.</p>
        )}
      </div>
    </>
  );
}

/* --- artifact fetch hook ------------------------------------------------- */

type ArtifactResult<T> = {
  state: "loading" | "loaded" | "missing";
  data: T | null;
};

/**
 * Fetch one per-session JSON artifact once. A missing file (404), an
 * unreachable server, or malformed JSON all resolve to `missing` with no
 * data — the record panel then shows a calm "no X recorded" line.
 */
function useJsonArtifact<T>(
  baseUrl: string,
  sessionId: string,
  file: string,
): ArtifactResult<T> {
  const [result, setResult] = useState<ArtifactResult<T>>({
    state: "loading",
    data: null,
  });

  useEffect(() => {
    let stopped = false;
    setResult({ state: "loading", data: null });

    (async () => {
      try {
        const res = await fetch(`${baseUrl}/${sessionId}/${file}`, {
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = (await res.json()) as T;
        if (!stopped) setResult({ state: "loaded", data });
      } catch {
        if (!stopped) setResult({ state: "missing", data: null });
      }
    })();

    return () => {
      stopped = true;
    };
  }, [baseUrl, sessionId, file]);

  return result;
}
