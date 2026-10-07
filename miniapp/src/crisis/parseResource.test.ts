import { describe, expect, it } from "vitest";

import { parseCrisisResource } from "./parseResource";

describe("parseCrisisResource", () => {
    it("splits number and description and keeps a dialable number", () => {
        expect(
            parseCrisisResource("8 (800) 2000-122 — детский телефон доверия, круглосуточно"),
        ).toEqual({
            title: "8 (800) 2000-122",
            description: "детский телефон доверия, круглосуточно",
            phone: "88002000122",
        });
    });

    it("keeps the whole line as a title when there is no separator", () => {
        expect(parseCrisisResource("Линия помощи")).toEqual({
            title: "Линия помощи",
            description: null,
            phone: null,
        });
    });

    it("does not offer a call link when the title has no digits", () => {
        expect(parseCrisisResource("Центр помощи — всегда рядом")).toEqual({
            title: "Центр помощи",
            description: "всегда рядом",
            phone: null,
        });
    });
});
