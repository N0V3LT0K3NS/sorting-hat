"use client";

import { useEffect, useState } from "react";
import styles from "../app/kiosk.module.css";

/**
 * A calm "building" progress indicator for the Active screen.
 *
 * Deliberately *not* a countdown. A soft arc grows as the conversation
 * accumulates and an understated minute count sits beneath it — enough to
 * orient a visitor ("this is progressing, it has shape") without the
 * pressure of a clock ticking down. The arc eases toward full over a
 * nominal span and then simply rests full; nothing flips to "time's up".
 *
 * `NOMINAL_MS` is the span the arc is calibrated to — the middle of the
 * ~10-15 min interview — so a typical interview ends with the arc nearly,
 * but not exactly, complete.
 */
const NOMINAL_MS = 12 * 60_000;

// Geometry for the SVG arc.
const SIZE = 64;
const STROKE = 2;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function SessionProgress() {
  const [elapsedMs, setElapsedMs] = useState(0);

  // Tick once a second from mount. The Active screen is remounted fresh
  // for every session (the `sessionKey` remount in Kiosk), so mount time
  // is a faithful session start.
  useEffect(() => {
    const start = Date.now();
    const t = setInterval(() => setElapsedMs(Date.now() - start), 1_000);
    return () => clearInterval(t);
  }, []);

  // Fraction of the arc that is filled — eased and capped just shy of full
  // so the visual keeps "building" gently rather than slamming to complete.
  const fraction = Math.min(elapsedMs / NOMINAL_MS, 1);
  const offset = CIRCUMFERENCE * (1 - fraction * 0.97);

  const minutes = Math.floor(elapsedMs / 60_000);
  const minutesLabel =
    minutes < 1 ? "just started" : `${minutes} min${minutes === 1 ? "" : "s"}`;

  return (
    <div className={styles.progress} aria-hidden>
      <svg
        className={styles.progressArc}
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
      >
        {/* The full track — the shape the conversation is filling in. */}
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          className={styles.progressTrack}
          strokeWidth={STROKE}
          fill="none"
        />
        {/* The accumulating arc itself. */}
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          className={styles.progressFill}
          strokeWidth={STROKE}
          fill="none"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
          strokeLinecap="round"
        />
      </svg>
      <span className={styles.progressLabel}>{minutesLabel}</span>
    </div>
  );
}
