"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "../app/kiosk.module.css";

/**
 * Screen 3 — Complete. The post-interview reveal.
 *
 * Two phases, driven by the offline portrait pipeline:
 *
 *   PHASE A — GENERATING (~90 s–3 min). Poll the delivery server's
 *     `GET /status/<session-id>` every couple of seconds and show a calm,
 *     stage-by-stage reveal — "Reading your interview…", "Drawing your
 *     portrait…" — so the wait feels like progress, not a frozen spinner.
 *
 *   PHASE B — REVEAL (`stage == "done"`). Show the finished portrait large on
 *     the screen with the QR code beside it, inviting the visitor to scan it
 *     and keep their portrait on their phone.
 *
 * Errors never strand a visitor: a pipeline error, an unreachable delivery
 * server, or a timeout all land on the same calm closing message.
 *
 * The delivery server lives on the kiosk machine; its base URL comes from
 * `NEXT_PUBLIC_DELIVERY_SERVER_URL` (see `.env.local.example`). The status
 * response gives `portrait_url` / `qr_url` as paths relative to that base —
 * the kiosk just resolves and displays them; it never generates the QR.
 */

/** Pipeline stages the delivery server reports. */
type Stage =
  | "pending"
  | "classifying"
  | "filling"
  | "rendering"
  | "delivering"
  | "done"
  | "error";

interface Status {
  session_id: string;
  stage: Stage;
  portrait_url: string | null;
  qr_url: string | null;
  error: string | null;
}

/** How often to poll the status endpoint while the pipeline runs. */
const POLL_INTERVAL_MS = 2_000;

/**
 * Give up if the pipeline has not reached `done` within this long. The
 * pipeline normally finishes in ~3 min; this is a generous ceiling so a
 * stuck render fails to a calm message rather than spinning forever.
 */
const REVEAL_TIMEOUT_MS = 5 * 60_000;

/** Calm, human-facing copy for each generating stage. */
const STAGE_COPY: Record<Stage, string> = {
  pending: "Gathering your interview…",
  classifying: "Reading your interview…",
  filling: "Finding your shape…",
  rendering: "Drawing your portrait…",
  delivering: "Almost there…",
  done: "",
  error: "",
};

/** The ordered generating stages — used to draw the step indicator. */
const STAGE_ORDER: Stage[] = [
  "classifying",
  "filling",
  "rendering",
  "delivering",
];

export function CompleteScreen({
  sessionId,
  onRevealDone,
}: {
  sessionId: string | null;
  onRevealDone: () => void;
}) {
  const baseUrl = (
    process.env.NEXT_PUBLIC_DELIVERY_SERVER_URL ?? "http://localhost:8808"
  ).replace(/\/+$/, "");

  // The latest status, the current stage, and a terminal-error flag.
  const [stage, setStage] = useState<Stage>("pending");
  const [status, setStatus] = useState<Status | null>(null);
  const [failed, setFailed] = useState(false);

  // `onRevealDone` fires exactly once, when the reveal lands (portrait shown
  // or graceful error) — Kiosk then holds briefly and resets to Idle.
  const revealDoneFired = useRef(false);
  const finishReveal = useCallback(() => {
    if (revealDoneFired.current) return;
    revealDoneFired.current = true;
    onRevealDone();
  }, [onRevealDone]);

  // Poll the delivery server until the pipeline reaches a terminal state.
  useEffect(() => {
    // No session id means the interview never produced one (e.g. the agent
    // never joined). Nothing to poll — close out calmly.
    if (!sessionId) {
      setFailed(true);
      finishReveal();
      return;
    }

    let stopped = false;
    const startedAt = Date.now();
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      if (stopped) return;

      // Generous overall ceiling — never spin forever on a stuck pipeline.
      if (Date.now() - startedAt > REVEAL_TIMEOUT_MS) {
        setFailed(true);
        finishReveal();
        return;
      }

      try {
        const res = await fetch(`${baseUrl}/status/${sessionId}`, {
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = (await res.json()) as Status;
        if (stopped) return;

        setStatus(data);
        setStage(data.stage);

        if (data.stage === "done") {
          finishReveal();
          return; // terminal — stop polling
        }
        if (data.stage === "error") {
          setFailed(true);
          finishReveal();
          return; // terminal — stop polling
        }
      } catch {
        // A single failed poll is not fatal — the delivery server may be
        // starting, or the wifi hiccuped. Keep polling; only the overall
        // timeout above ends the wait. The screen stays on its calm copy.
      }

      timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId, baseUrl, finishReveal]);

  // PHASE B — REVEAL. The portrait is ready: show it large with the QR.
  if (stage === "done" && status?.portrait_url) {
    const portraitSrc = `${baseUrl}${status.portrait_url}`;
    const qrSrc = status.qr_url ? `${baseUrl}${status.qr_url}` : null;
    return (
      <div className={styles.screen}>
        <p className={styles.revealTitle}>Here you are.</p>
        <div className={styles.revealLayout}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            className={styles.revealPortrait}
            src={portraitSrc}
            alt="Your sorting-hat portrait"
          />
          {qrSrc && (
            <div className={styles.revealQr}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                className={styles.revealQrImage}
                src={qrSrc}
                alt="QR code to open your portrait on your phone"
              />
              <p className={styles.revealQrCaption}>
                Scan to keep it on your phone
              </p>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ERROR / TIMEOUT — a calm closing message. Never a broken screen.
  if (failed || stage === "error") {
    return (
      <div className={styles.screen}>
        <div className={styles.completeMark} aria-hidden />
        <p className={styles.prompt}>Thank you.</p>
        <p className={styles.subPrompt}>
          Your portrait is on its way — ask an attendant if you&rsquo;d like
          to see it.
        </p>
      </div>
    );
  }

  // PHASE A — GENERATING. The stage-by-stage reveal.
  return (
    <div className={styles.screen}>
      <p className={styles.prompt}>Making your portrait.</p>
      {/* The `key` remounts this line on each stage change, so the copy
          fades in fresh — the reveal feels like it is advancing. */}
      <p key={stage} className={styles.revealStage}>
        {STAGE_COPY[stage]}
      </p>

      {/* A row of steps that fill as the pipeline advances — a sense of
          progression, not a frozen loader. */}
      <div className={styles.stageDots} aria-hidden>
        {STAGE_ORDER.map((s) => (
          <span
            key={s}
            className={styles.stageDot}
            data-state={stageState(stage, s)}
          />
        ))}
      </div>

      <p className={styles.subPrompt}>This takes a couple of minutes.</p>
    </div>
  );
}

/**
 * Where stage `s` sits relative to the current stage: a step is `done` once
 * the pipeline has moved past it, `active` while it is running, and `upcoming`
 * before it begins. `pending` (pre-pipeline) leaves every step upcoming.
 */
function stageState(
  current: Stage,
  s: Stage,
): "done" | "active" | "upcoming" {
  const order: Stage[] = ["classifying", "filling", "rendering", "delivering"];
  const ci = order.indexOf(current);
  const si = order.indexOf(s);
  if (ci < 0) return "upcoming"; // current is "pending" — nothing started yet
  if (si < ci) return "done";
  if (si === ci) return "active";
  return "upcoming";
}
