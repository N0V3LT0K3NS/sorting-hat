import { Kiosk } from "../components/Kiosk";

/**
 * The kiosk is a single page. All interactivity lives in <Kiosk />, which is
 * a client component holding the idle -> active -> complete state machine.
 */
export default function Page() {
  return <Kiosk />;
}
