"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "../../app/shape-interface.module.css";
import { ShapeField } from "./ShapeField";

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

const POLL_INTERVAL_MS = 2_000;
const REVEAL_TIMEOUT_MS = 5 * 60_000;

const STAGE_COPY: Record<Stage, string> = {
  pending: "Gathering your interview",
  classifying: "Reading your interview",
  filling: "Finding your shape",
  rendering: "Drawing your portrait",
  delivering: "Almost there",
  done: "",
  error: "",
};

const STAGE_ORDER: Stage[] = [
  "classifying",
  "filling",
  "rendering",
  "delivering",
];

export function ShapeCompleteScreen({
  sessionId,
  onRevealDone,
}: {
  sessionId: string | null;
  onRevealDone: () => void;
}) {
  const baseUrl = (
    process.env.NEXT_PUBLIC_DELIVERY_SERVER_URL ?? "http://localhost:8808"
  ).replace(/\/+$/, "");
  const [stage, setStage] = useState<Stage>("pending");
  const [status, setStatus] = useState<Status | null>(null);
  const [failed, setFailed] = useState(false);
  const revealDoneFired = useRef(false);

  const finishReveal = useCallback(() => {
    if (revealDoneFired.current) return;
    revealDoneFired.current = true;
    onRevealDone();
  }, [onRevealDone]);

  useEffect(() => {
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
          return;
        }
        if (data.stage === "error") {
          setFailed(true);
          finishReveal();
          return;
        }
      } catch {
        // Keep polling until the generous timeout.
      }

      timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [baseUrl, finishReveal, sessionId]);

  if (stage === "done" && status?.portrait_url) {
    const portraitSrc = `${baseUrl}${status.portrait_url}`;
    const qrSrc = status.qr_url ? `${baseUrl}${status.qr_url}` : null;
    return (
      <div className={styles.screen}>
        <ShapeField phase="reveal" outputLevel={0.4} />
        <section className={styles.revealPanel}>
          <p className={styles.kicker}>portrait resolved</p>
          <h1 className={styles.revealTitle}>Here you are.</h1>
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
        </section>
      </div>
    );
  }

  if (failed || stage === "error") {
    return (
      <div className={styles.screen}>
        <ShapeField phase="error" />
        <section className={styles.copyPanel}>
          <p className={styles.kicker}>soft landing</p>
          <h1 className={styles.title}>Thank you.</h1>
          <p className={styles.body}>
            Your portrait is on its way. Ask an attendant if you would like to
            see it.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className={styles.screen}>
      <ShapeField phase="generating" outputLevel={0.18} />
      <section className={styles.copyPanel}>
        <p className={styles.kicker}>reveal pipeline</p>
        <h1 className={styles.title}>Making your portrait.</h1>
        <p key={stage} className={styles.stageLine}>
          {STAGE_COPY[stage]}
        </p>
        <div className={styles.stageDots} aria-hidden>
          {STAGE_ORDER.map((s) => (
            <span
              key={s}
              className={styles.stageDot}
              data-state={stageState(stage, s)}
            />
          ))}
        </div>
        <p className={styles.body}>This takes a couple of minutes.</p>
      </section>
    </div>
  );
}

function stageState(
  current: Stage,
  s: Stage,
): "done" | "active" | "upcoming" {
  const order: Stage[] = ["classifying", "filling", "rendering", "delivering"];
  const ci = order.indexOf(current);
  const si = order.indexOf(s);
  if (ci < 0) return "upcoming";
  if (si < ci) return "done";
  if (si === ci) return "active";
  return "upcoming";
}
