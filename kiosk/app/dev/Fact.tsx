"use client";

import styles from "./dev.module.css";

/** One label/value pair in an interview-state / result facts grid. */
export function Fact({
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
