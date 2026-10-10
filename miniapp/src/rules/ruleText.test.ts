import { describe, expect, it } from "vitest";

import { codePointLength, normalizeRuleText, validateRuleText } from "./ruleText";

describe("ruleText", () => {
    it("normalizes CRLF and collapses blank lines", () => {
        expect(normalizeRuleText("a\r\n\r\n\r\nb")).toBe("a\n\nb");
        expect(normalizeRuleText("a\rb")).toBe("a\nb");
    });

    it("strips trailing spaces per line", () => {
        expect(normalizeRuleText("hello  \nworld   ")).toBe("hello\nworld");
    });

    it("counts emoji as one code point", () => {
        expect(codePointLength("😀")).toBe(1);
        const max = 10;
        const text = `${"x".repeat(9)}😀`;
        expect(validateRuleText(text, max)).toEqual({ ok: true, value: text });
        expect(validateRuleText(`${text}y`, max).ok).toBe(false);
    });

    it("rejects empty, too long, and control chars", () => {
        expect(validateRuleText("   ", 500)).toMatchObject({ ok: false, error: "empty" });
        expect(validateRuleText("a\tb", 500)).toMatchObject({ ok: false, error: "invalid_chars" });
        expect(validateRuleText("a\u007fb", 500)).toMatchObject({
            ok: false,
            error: "invalid_chars",
        });
        expect(validateRuleText("x".repeat(501), 500)).toMatchObject({
            ok: false,
            error: "too_long",
            actual: 501,
        });
    });

    it("allows newlines in rule text", () => {
        expect(validateRuleText("строка1\nстрока2", 500)).toEqual({
            ok: true,
            value: "строка1\nстрока2",
        });
    });
});
