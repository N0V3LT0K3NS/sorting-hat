"use client";

import { Suspense, useCallback, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import styles from "./dev.module.css";
import { SessionDetail } from "./SessionDetail";
import { SessionIndex } from "./SessionIndex";

/**
 * /dev — the developer session dashboard.
 *
 * A separate route, deliberately away from the visitor kiosk flow at `/`. It
 * is a two-level local dashboard over every interview the machine has run:
 *
 *   - INDEX (`/dev`)               — a live list of all sessions, polling the
 *                                    delivery server's `GET /sessions`.
 *   - DETAIL (`/dev?session=<id>`) — one session in full: the live classifier
 *                                    signals while it runs, or — once it is
 *                                    done — the portrait, classification,
 *                                    filled result, and full transcript.
 *
 * The selected session lives in the `?session=` query param so a detail view
 * is linkable and survives a refresh. This route is a tool, not a visitor
 * surface: clarity over polish. It never touches the kiosk flow.
 */

function DevDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = (searchParams.get("session") ?? "").trim();

  const baseUrl = useMemo(
    () =>
      (
        process.env.NEXT_PUBLIC_DELIVERY_SERVER_URL ?? "http://localhost:8808"
      ).replace(/\/+$/, ""),
    [],
  );

  // Navigate to a session's detail view — sets `?session=<id>`.
  const openSession = useCallback(
    (id: string) => {
      router.push(`/dev?session=${encodeURIComponent(id)}`);
    },
    [router],
  );

  // Back to the index — clears the query param.
  const backToIndex = useCallback(() => {
    router.push("/dev");
  }, [router]);

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        {!sessionId ? (
          <>
            <div className={styles.header}>
              <span className={styles.title}>
                sorting-hat · session dashboard
              </span>
              <span className={styles.subtitle}>
                Every interview this machine has run. Diagnostic only — not
                part of the visitor kiosk flow.
              </span>
            </div>
            <SessionIndex baseUrl={baseUrl} onOpen={openSession} />
          </>
        ) : (
          <SessionDetail
            sessionId={sessionId}
            baseUrl={baseUrl}
            onBack={backToIndex}
          />
        )}
      </div>
    </div>
  );
}

/**
 * `useSearchParams` requires a Suspense boundary in the App Router, so the
 * dashboard is wrapped here. The fallback is brief and calm.
 */
export default function DevPage() {
  return (
    <Suspense
      fallback={
        <div className={styles.page}>
          <div className={styles.inner}>
            <p className={styles.empty}>Loading…</p>
          </div>
        </div>
      }
    >
      <DevDashboard />
    </Suspense>
  );
}
