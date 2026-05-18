/**
 * Shared types and small helpers for the /dev session dashboard.
 *
 * The shapes here mirror the delivery server (delivery/server.py) — the
 * authoritative source for /sessions, /live, /status and the per-session
 * JSON artifacts. All endpoints degrade gracefully and never 500, so the
 * dashboard always has a uniform shape to parse.
 */

/** The four classifier signals, in display order. */
export const SIGNAL_KEYS = ["iceberg", "two_buttons", "compass", "arc"] as const;
export type SignalKey = (typeof SIGNAL_KEYS)[number];

/** Interview phases the `/live` and `/sessions` endpoints may report. */
export type Phase =
  | "unknown"
  | "pending"
  | "base_questions"
  | "probing"
  | "complete";

/** Offline-pipeline stages a `status.json` may report. */
export type Stage =
  | "pending"
  | "classifying"
  | "filling"
  | "rendering"
  | "delivering"
  | "done"
  | "error";

/** One entry of `GET /sessions` -> `{ sessions: SessionSummary[] }`. */
export interface SessionSummary {
  session_id: string;
  phase: Phase;
  pipeline_stage: Stage | null;
  turn_count: number;
  chosen_template: string | null;
  has_transcript: boolean;
  has_portrait: boolean;
  has_classification: boolean;
  updated_at: string | null;
  portrait_url: string | null;
}

/** The `GET /live/<session-id>` JSON shape. */
export interface LiveState {
  session_id: string;
  phase: Phase;
  base_questions_completed: number;
  base_questions_total: number;
  signals: Record<SignalKey, number>;
  leading_template: string | null;
  chosen_template: string | null;
  routing_done: boolean;
  turn_count: number;
  updated_at: string | null;
}

/** The `GET /status/<session-id>` JSON shape. */
export interface PipelineStatus {
  session_id: string;
  stage: Stage;
  portrait_url: string | null;
  qr_url: string | null;
  error: string | null;
}

/** `classification.json` — the routing decision. */
export interface Classification {
  template?: string;
  confidence?: number;
  reasoning?: string;
}

/** One turn of `transcript.json`. */
export interface TranscriptTurn {
  speaker?: string;
  text?: string;
}

/**
 * A session's coarse status, derived from its summary — used for the index
 * badge and to route the detail view to the right sub-panel.
 *
 * - `active`   — the interview is running (phase base_questions / probing).
 * - `pipeline` — the interview is done; the offline portrait pipeline is
 *                mid-run (a non-terminal pipeline stage).
 * - `complete` — the pipeline finished (`done`).
 * - `error`    — the pipeline reported an error.
 * - `idle`     — anything else: a folder with a transcript but no live state,
 *                or a session that never produced state.
 */
export type DerivedStatus = "active" | "pipeline" | "complete" | "error" | "idle";

/** Derive the coarse status of a session from its index summary. */
export function deriveStatus(s: {
  phase: Phase;
  pipeline_stage: Stage | null;
}): DerivedStatus {
  if (s.phase === "base_questions" || s.phase === "probing") return "active";
  if (s.pipeline_stage === "error") return "error";
  if (s.pipeline_stage === "done") return "complete";
  if (s.pipeline_stage && s.pipeline_stage !== "pending") return "pipeline";
  return "idle";
}

/** Human-readable badge label for each derived status. */
export const STATUS_LABEL: Record<DerivedStatus, string> = {
  active: "active",
  pipeline: "rendering",
  complete: "complete",
  error: "error",
  idle: "idle",
};

/** Badge colour tone (a `data-tone` attribute the CSS keys off). */
export const STATUS_TONE: Record<DerivedStatus, string> = {
  active: "active",
  pipeline: "pipeline",
  complete: "complete",
  error: "error",
  idle: "idle",
};

/** Human-readable label for each interview phase. */
export const PHASE_LABEL: Record<Phase, string> = {
  unknown: "unknown — no live state",
  pending: "pending — no state yet",
  base_questions: "base questions",
  probing: "probing",
  complete: "complete",
};

/** Calm copy for each offline-pipeline stage (mirrors CompleteScreen). */
export const STAGE_LABEL: Record<Stage, string> = {
  pending: "waiting to start",
  classifying: "reading the interview",
  filling: "finding the shape",
  rendering: "drawing the portrait",
  delivering: "almost there",
  done: "done",
  error: "error",
};

/** The ordered, non-terminal pipeline stages — used to draw the step row. */
export const STAGE_ORDER: Stage[] = [
  "classifying",
  "filling",
  "rendering",
  "delivering",
];

/**
 * Format an ISO-8601 timestamp as a short relative string ("3m ago"). Falls
 * back to the raw value, then to a dash, so a missing/odd timestamp is calm.
 */
export function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const sec = Math.round(diffMs / 1000);
  if (sec < 0) return "just now";
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}
