/** Client-side RuleText rules mirroring the domain value object. */

const CRLF = /\r\n|\r/g;
const MULTI_BLANK = /\n{3,}/g;

export function codePointLength(text: string): number {
    return Array.from(text).length;
}

export function normalizeRuleText(raw: string): string {
    let normalized = raw.normalize("NFC");
    normalized = normalized.replace(CRLF, "\n");
    normalized = normalized
        .split("\n")
        .map((line) => line.replace(/\s+$/u, ""))
        .join("\n");
    normalized = normalized.replace(MULTI_BLANK, "\n\n");
    return normalized.trim();
}

export type RuleTextClientError = "empty" | "too_long" | "invalid_chars";

function isForbiddenControl(char: string): boolean {
    if (char === "\n") {
        return false;
    }
    const code = char.codePointAt(0);
    if (code === undefined) {
        return false;
    }
    // Unicode Cc: C0 controls, DEL, and C1 controls (U+0080–U+009F).
    return code < 32 || code === 0x7f || (code >= 0x80 && code <= 0x9f);
}

export function validateRuleText(
    raw: string,
    maxChars: number,
): { ok: true; value: string } | { ok: false; error: RuleTextClientError; actual: number } {
    const normalized = normalizeRuleText(raw);
    if (normalized.length === 0) {
        return { ok: false, error: "empty", actual: 0 };
    }
    for (const char of normalized) {
        if (isForbiddenControl(char)) {
            return { ok: false, error: "invalid_chars", actual: codePointLength(normalized) };
        }
    }
    const actual = codePointLength(normalized);
    if (actual > maxChars) {
        return { ok: false, error: "too_long", actual };
    }
    return { ok: true, value: normalized };
}
