import { describe, it, expect, beforeEach } from "vitest";
import { rememberOpened, takeReturnRow } from "./returnRow";

describe("return row (P5)", () => {
  beforeEach(() => sessionStorage.clear());

  it("hands back the last experiment opened in a program, once", () => {
    rememberOpened("p1", "p1-c3");
    rememberOpened("p1", "p1-c4");          // the later one wins
    expect(takeReturnRow("p1")).toBe("p1-c4");
    expect(takeReturnRow("p1")).toBeNull();
  });

  it("keeps programs apart", () => {
    rememberOpened("p1", "p1-c3");
    expect(takeReturnRow("p2")).toBeNull();
    expect(takeReturnRow("p1")).toBe("p1-c3");
  });
});
