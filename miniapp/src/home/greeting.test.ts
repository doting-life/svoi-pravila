import { describe, expect, it } from "vitest";

import { ru } from "../localization/ru";
import { greetingFor } from "./greeting";

function at(hour: number): Date {
    return new Date(2026, 9, 7, hour, 30);
}

describe("greetingFor", () => {
    it.each([
        [0, ru.greetingNight],
        [4, ru.greetingNight],
        [5, ru.greetingMorning],
        [11, ru.greetingMorning],
        [12, ru.greetingDay],
        [17, ru.greetingDay],
        [18, ru.greetingEvening],
        [22, ru.greetingEvening],
        [23, ru.greetingNight],
    ])("uses the local hour %i", (hour, expected) => {
        expect(greetingFor(at(hour))).toBe(expected);
    });
});
