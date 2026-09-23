/** One way to write a time or a date, whatever locale the browser reports (P2).
 *
 *  The browser's locale used to decide: a server's "not answering since" read
 *  `23.03` on a machine whose locale writes times with a dot, and the same view
 *  showed "3:20 PM" on another. Times here are always 24-hour `HH:MM` and dates
 *  always `22 Sep` / `22 Sep 2026`, in the viewer's own time zone. Month names
 *  come from a fixed list rather than `Intl`, so no locale can reach them. */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const two = (n: number) => String(n).padStart(2, "0");

function at(epochSeconds: number): Date {
  return new Date(epochSeconds * 1000);
}

/** "23:05" */
export function hhmm(epochSeconds: number): string {
  const d = at(epochSeconds);
  return `${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** "22 Sep" */
export function dayMonth(epochSeconds: number): string {
  const d = at(epochSeconds);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** "22 Sep 2026" */
export function dayMonthYear(epochSeconds: number): string {
  return `${dayMonth(epochSeconds)} ${at(epochSeconds).getFullYear()}`;
}

/** "22 Sep, 23:05" — for lists where the year is obvious. */
export function dayTime(epochSeconds: number): string {
  return `${dayMonth(epochSeconds)}, ${hhmm(epochSeconds)}`;
}

/** "22 Sep 2026, 23:05" */
export function fullTime(epochSeconds: number): string {
  return `${dayMonthYear(epochSeconds)}, ${hhmm(epochSeconds)}`;
}
