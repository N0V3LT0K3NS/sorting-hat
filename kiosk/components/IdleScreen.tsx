"use client";

import styles from "../app/kiosk.module.css";

/**
 * Screen 1 — Idle. A person walks up to this. One large button.
 */
export function IdleScreen({
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
      <p className={styles.prompt}>Ready when you are.</p>
      <button
        className={styles.beginButton}
        onClick={onBegin}
        disabled={connecting}
        aria-label="Press to begin the interview"
      >
        {connecting ? "Connecting…" : "Press to begin"}
      </button>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
