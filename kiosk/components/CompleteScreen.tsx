"use client";

import styles from "../app/kiosk.module.css";

/**
 * Screen 3 — Complete. A calm confirmation. No call to action; an attendant
 * or a timed reset returns the kiosk to idle for the next visitor.
 */
export function CompleteScreen() {
  return (
    <div className={styles.screen}>
      <div className={styles.completeMark} aria-hidden />
      <p className={styles.prompt}>Thank you.</p>
      <p className={styles.subPrompt}>Your portrait is being made.</p>
    </div>
  );
}
