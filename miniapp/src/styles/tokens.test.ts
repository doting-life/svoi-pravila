import { describe, expect, it } from "vitest";

import tokensCss from "./tokens.css?raw";

type Scheme = "light" | "dark";
type Palette = Readonly<Record<string, string>>;

function paletteFor(scheme: Scheme): Palette {
    const selector = `:root[data-color-scheme="${scheme}"]`;
    const start = tokensCss.indexOf(selector);
    expect(start, `${scheme} block`).toBeGreaterThanOrEqual(0);
    const block = tokensCss.slice(tokensCss.indexOf("{", start) + 1, tokensCss.indexOf("}", start));
    const palette: Record<string, string> = {};
    for (const match of block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6});/g)) {
        const [, name, value] = match;
        if (name !== undefined && value !== undefined) {
            palette[name] = value;
        }
    }
    return palette;
}

function channel(value: number): number {
    const normalized = value / 255;
    return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
    const red = channel(Number.parseInt(hex.slice(1, 3), 16));
    const green = channel(Number.parseInt(hex.slice(3, 5), 16));
    const blue = channel(Number.parseInt(hex.slice(5, 7), 16));
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(foreground: string, background: string): number {
    const first = luminance(foreground);
    const second = luminance(background);
    return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

const BODY_PAIRS: readonly (readonly [string, string])[] = [
    ["ink", "bg"],
    ["ink", "surface"],
    ["ink", "sunken"],
    ["ink", "accent-soft"],
    ["ink", "warm-soft"],
    ["ink", "danger-soft"],
    ["ink-2", "bg"],
    ["ink-2", "surface"],
    ["ink-2", "sunken"],
    ["ink-3", "bg"],
    ["ink-3", "surface"],
    ["on-accent", "accent"],
    ["accent", "bg"],
    ["accent", "surface"],
    ["accent", "accent-soft"],
    ["warm", "bg"],
    ["warm", "surface"],
    ["warm", "warm-soft"],
    ["danger", "bg"],
    ["danger", "surface"],
    ["danger", "danger-soft"],
];

const LARGE_PAIRS: readonly (readonly [string, string])[] = [
    ["ink-3", "sunken"],
    ["accent-hover", "surface"],
    ["accent-hover", "bg"],
];

describe("design tokens", () => {
    it.each<Scheme>(["light", "dark"])("defines the full %s palette", (scheme) => {
        const palette = paletteFor(scheme);
        expect(Object.keys(palette).sort()).toEqual(
            [
                "accent",
                "accent-hover",
                "accent-soft",
                "bg",
                "danger",
                "danger-soft",
                "ink",
                "ink-2",
                "ink-3",
                "line",
                "on-accent",
                "sunken",
                "surface",
                "warm",
                "warm-soft",
            ].sort(),
        );
    });

    describe.each<Scheme>(["light", "dark"])("%s contrast", (scheme) => {
        const palette = paletteFor(scheme);

        it.each(BODY_PAIRS)("%s on %s reaches 4.5:1 for body text", (foreground, background) => {
            const fg = palette[foreground];
            const bg = palette[background];
            expect(fg).toBeDefined();
            expect(bg).toBeDefined();
            expect(contrastRatio(fg as string, bg as string)).toBeGreaterThanOrEqual(4.5);
        });

        it.each(LARGE_PAIRS)("%s on %s reaches 3:1 for large text", (foreground, background) => {
            const fg = palette[foreground];
            const bg = palette[background];
            expect(fg).toBeDefined();
            expect(bg).toBeDefined();
            expect(contrastRatio(fg as string, bg as string)).toBeGreaterThanOrEqual(3);
        });
    });
});
