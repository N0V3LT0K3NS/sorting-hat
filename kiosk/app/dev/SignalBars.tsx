"use client";

import styles from "./dev.module.css";
import { SIGNAL_KEYS, SignalKey } from "./types";

/**
 * The four classifier signal weights, rendered as horizontal bars.
 *
 * Extracted from the original single-session dev view so both the live
 * detail panel and any future readout can share one bar component. Bar fills
 * are scaled against the largest signal so movement stays visible even when
 * weights are small; the leading signal's bar is shown in the accent colour.
 */
export function SignalBars({
  signals,
  leadingTemplate,
}: {
  signals: Record<SignalKey, number>;
  leadingTemplate: string | null;
}) {
  const safe: Record<SignalKey, number> = {
    iceberg: Number(signals?.iceberg) || 0,
    two_buttons: Number(signals?.two_buttons) || 0,
    compass: Number(signals?.compass) || 0,
    arc: Number(signals?.arc) || 0,
  };

  // Never divide by zero — keep a tiny floor.
  const maxSignal = Math.max(...SIGNAL_KEYS.map((k) => safe[k]), 0.0001);

  // Which bar to emphasise: the leading template names a signal family, so a
  // direct key match highlights that bar. Otherwise fall back to the
  // strongest signal once any turn has been classified.
  const leadingByName = SIGNAL_KEYS.find((k) => k === leadingTemplate);
  const strongest = SIGNAL_KEYS.reduce((best, k) =>
    safe[k] > safe[best] ? k : best,
  );
  const leadingKey: SignalKey | null =
    leadingByName ?? (maxSignal > 0.0001 ? strongest : null);

  return (
    <div className={styles.bars}>
      {SIGNAL_KEYS.map((key) => {
        const value = safe[key];
        const fillPct = Math.min((value / maxSignal) * 100, 100);
        return (
          <div
            key={key}
            className={styles.bar}
            data-leading={leadingKey === key}
          >
            <span className={styles.barName}>{key}</span>
            <div className={styles.barTrack}>
              <div
                className={styles.barFill}
                style={{ width: `${fillPct}%` }}
              />
            </div>
            <span className={styles.barValue}>{value.toFixed(3)}</span>
          </div>
        );
      })}
    </div>
  );
}
