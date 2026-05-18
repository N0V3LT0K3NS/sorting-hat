"use client";

import { useEffect, useState } from "react";
import styles from "../../app/shape-interface.module.css";

const NOMINAL_MS = 12 * 60_000;
const SIZE = 64;
const STROKE = 2;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function ShapeSessionProgress() {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const t = setInterval(() => setElapsedMs(Date.now() - start), 1_000);
    return () => clearInterval(t);
  }, []);

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
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          className={styles.progressTrack}
          strokeWidth={STROKE}
          fill="none"
        />
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
