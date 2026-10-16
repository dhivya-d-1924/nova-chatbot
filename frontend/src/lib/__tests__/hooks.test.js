import { describe, expect, it } from "vitest";
import { formatWhen } from "../hooks";

describe("formatWhen", () => {
  it("formats recent and older timestamps", () => {
    const now = Date.now();
    expect(formatWhen(now * 1000)).toBe("just now");
    expect(formatWhen((now - 5 * 60 * 1000) * 1000)).toBe("5 min ago");
    expect(formatWhen(0)).toBe("");
    expect(formatWhen(new Date(2020, 0, 2).getTime() * 1000)).toMatch(/2020/);
  });
});
