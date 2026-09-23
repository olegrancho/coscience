import { describe, it, expect } from "vitest";
import { dayMonth, dayMonthYear, dayTime, fullTime, hhmm } from "./timefmt";

// Built from local fields, so the assertions hold in whatever zone the tests run.
const t = (y: number, mo: number, d: number, h: number, mi: number) =>
  new Date(y, mo, d, h, mi).getTime() / 1000;

describe("timefmt (P2)", () => {
  it("writes times as 24-hour HH:MM, zero-padded", () => {
    expect(hhmm(t(2026, 8, 22, 23, 3))).toBe("23:03");
    expect(hhmm(t(2026, 8, 22, 7, 5))).toBe("07:05");
    expect(hhmm(t(2026, 8, 22, 0, 0))).toBe("00:00");
  });

  it("writes dates day-first with an English month, never the locale's", () => {
    expect(dayMonth(t(2026, 8, 2, 12, 0))).toBe("2 Sep");
    expect(dayMonthYear(t(2026, 0, 14, 12, 0))).toBe("14 Jan 2026");
  });

  it("joins the two the same way everywhere", () => {
    expect(dayTime(t(2026, 11, 31, 15, 20))).toBe("31 Dec, 15:20");
    expect(fullTime(t(2026, 11, 31, 15, 20))).toBe("31 Dec 2026, 15:20");
  });

  it("does not move when the browser's locale does", () => {
    const at = t(2026, 8, 22, 23, 3);
    const spy = Date.prototype.toLocaleTimeString;
    Date.prototype.toLocaleTimeString = () => "23.03";   // what a dot-locale produced
    try {
      expect(hhmm(at)).toBe("23:03");
    } finally {
      Date.prototype.toLocaleTimeString = spy;
    }
  });
});
