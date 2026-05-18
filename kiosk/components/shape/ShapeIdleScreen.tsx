"use client";

import styles from "../../app/shape-interface.module.css";
import { ShapeField } from "./ShapeField";

export function ShapeIdleScreen({
  onBegin,
  connecting,
  error,
}: {
  onBegin: () => void;
  connecting: boolean;
  error: string | null;
}) {
  return (
    <div className={styles.screen}>
      <ShapeField phase={connecting ? "connecting" : "idle"} />
      <section className={styles.copyPanel}>
        <p className={styles.kicker}>voice in / portrait out</p>
        <h1 className={styles.title}>Sit with the machine.</h1>
        <p className={styles.body}>
          A private voice interview becomes a rendered shape of you. No survey.
          No visible sorting. Speak naturally; the portrait arrives at the end.
        </p>
        <button
          className={styles.primaryButton}
          onClick={onBegin}
          disabled={connecting}
          aria-label="Press to begin the interview"
        >
          {connecting ? "Opening the room" : "Enter the field"}
        </button>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
      </section>
    </div>
  );
}
