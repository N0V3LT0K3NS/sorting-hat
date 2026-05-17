"use client";

/**
 * Kiosk-mode hardening — small browser guards that keep a dedicated,
 * unattended station from drifting out of the app.
 *
 * None of this replaces a proper OS-level kiosk launch (see
 * `kiosk/README.md`), but it removes the easy ways a visitor can break the
 * experience with a stray gesture: a right-click menu, a pinch-zoom, a
 * drag-and-drop, an accidental text selection.
 */

/**
 * Install kiosk-mode event guards on `window`. Returns a cleanup function
 * that removes every listener — call it on unmount so React strict-mode
 * double-invocation and hot reload do not stack duplicate handlers.
 */
export function installKioskGuards(): () => void {
  const prevent = (e: Event) => e.preventDefault();

  // No right-click / long-press context menu.
  window.addEventListener("contextmenu", prevent);
  // No drag-and-drop of text or images out of (or around) the page.
  window.addEventListener("dragstart", prevent);
  window.addEventListener("drop", prevent);
  // No pinch-zoom / ctrl+wheel zoom that would leave content off-screen.
  const onWheel = (e: WheelEvent) => {
    if (e.ctrlKey) e.preventDefault();
  };
  window.addEventListener("wheel", onWheel, { passive: false });
  // No keyboard zoom or browser shortcuts that navigate away.
  const onKeyDown = (e: KeyboardEvent) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && ["+", "-", "=", "0"].includes(e.key)) e.preventDefault();
  };
  window.addEventListener("keydown", onKeyDown);

  return () => {
    window.removeEventListener("contextmenu", prevent);
    window.removeEventListener("dragstart", prevent);
    window.removeEventListener("drop", prevent);
    window.removeEventListener("wheel", onWheel);
    window.removeEventListener("keydown", onKeyDown);
  };
}

/**
 * Best-effort request for browser fullscreen.
 *
 * Browsers only honour `requestFullscreen()` inside a user gesture, so this
 * is called from the start-button handler — not on load. A rejection (the
 * gesture expired, the browser refused) is swallowed: the OS-level kiosk
 * launch flag is the real fullscreen guarantee; this is a convenience for
 * the common case of launching the app by hand.
 */
export async function requestFullscreen(): Promise<void> {
  const el = document.documentElement;
  if (document.fullscreenElement || !el.requestFullscreen) return;
  try {
    await el.requestFullscreen();
  } catch {
    /* not fatal — see doc comment */
  }
}
