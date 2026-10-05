import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { formatDisplayDate } from "../dates/formatDisplayDate";
import { formatAppliedRuleCitation } from "./formatAppliedRuleCitation";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const fixture = readFileSync(
    join(repoRoot, "testdata/decode_rule_citation_line.txt"),
    "utf8",
).trim();

describe("formatAppliedRuleCitation", () => {
    it("matches the shared bot/mini-app citation fixture", () => {
        const template = "Учтено правило от {date}: «{text}»";
        const date = formatDisplayDate(
            "2026-10-03T12:00:00.000Z",
            "Europe/Moscow",
            new Date("2026-10-04T12:00:00.000Z"),
        );
        expect(
            formatAppliedRuleCitation(template, { date, text: "не повышать голос" }),
        ).toBe(fixture);
    });
});
