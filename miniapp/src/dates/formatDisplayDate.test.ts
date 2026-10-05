import { describe, expect, it } from "vitest";

import { formatDisplayDate } from "./formatDisplayDate";

describe("formatDisplayDate", () => {
    it("omits the year in the same calendar year", () => {
        const now = new Date("2026-10-04T12:00:00.000Z");
        expect(formatDisplayDate("2026-10-03T12:00:00.000Z", "Europe/Moscow", now)).toBe(
            "3 октября",
        );
    });

    it("includes the year for a past calendar year", () => {
        const now = new Date("2026-10-04T12:00:00.000Z");
        expect(formatDisplayDate("2025-01-15T12:00:00.000Z", "Europe/Moscow", now)).toBe(
            "15 января 2025",
        );
    });

    it("crosses the Moscow day boundary from 23:30 UTC", () => {
        const now = new Date("2026-10-04T12:00:00.000Z");
        // 23:30 UTC is 02:30 next day in Europe/Moscow (UTC+3).
        expect(formatDisplayDate("2026-10-03T23:30:00.000Z", "Europe/Moscow", now)).toBe(
            "4 октября",
        );
    });
});
