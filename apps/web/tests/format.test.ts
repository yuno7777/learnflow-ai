import { describe, expect, it } from "vitest";

import { bytes, compactNumber, currency, duration, number, percent } from "@/lib/format";

describe("format helpers", () => {
  it("renders currency with more precision for small amounts", () => {
    expect(currency(0.000123)).toBe("$0.0001");
    expect(currency(12.5)).toBe("$12.50");
  });

  it("renders an em dash rather than a misleading zero for missing values", () => {
    // A missing metric and a metric that is genuinely zero must not look identical.
    expect(currency(null)).toBe("—");
    expect(number(undefined)).toBe("—");
    expect(percent(null)).toBe("—");
    expect(duration(null)).toBe("—");
    expect(number(0)).toBe("0");
  });

  it("formats durations by magnitude", () => {
    expect(duration(450)).toBe("450ms");
    expect(duration(1500)).toBe("1.5s");
    expect(duration(65_000)).toBe("1m 5s");
  });

  it("formats byte sizes", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(2048)).toBe("2.0 KB");
    expect(bytes(0)).toBe("—");
  });

  it("compacts large counts", () => {
    expect(compactNumber(1500)).toBe("1.5K");
    expect(percent(0.8534)).toBe("85.3%");
  });
});
