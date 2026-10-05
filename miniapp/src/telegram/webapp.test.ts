import { describe, expect, it, vi } from "vitest";

import {
    applyThemeCssVariables,
    createTelegramAdapter,
    resetTelegramAdapterGuardsForTests,
} from "./webapp";

function installMockWebApp(initData: string): {
    ready: ReturnType<typeof vi.fn>;
    expand: ReturnType<typeof vi.fn>;
} {
    const ready = vi.fn();
    const expand = vi.fn();
    Object.defineProperty(window, "Telegram", {
        configurable: true,
        value: {
            WebApp: {
                initData,
                colorScheme: "dark",
                themeParams: {
                    bg_color: "#101010",
                    text_color: "#eeeeee",
                },
                ready,
                expand,
            },
        },
    });
    return { ready, expand };
}

describe("createTelegramAdapter", () => {
    it("reports outside Telegram when initData is empty", () => {
        installMockWebApp("");
        const adapter = createTelegramAdapter();
        expect(adapter.isInsideTelegram).toBe(false);
        expect(adapter.initData).toBe("");
        adapter.ready();
        adapter.expand();
    });

    it("maps theme params and calls ready/expand once inside Telegram", () => {
        const { ready, expand } = installMockWebApp("query_id=1&user=%7B%7D");
        const adapter = createTelegramAdapter();
        expect(adapter.isInsideTelegram).toBe(true);
        expect(adapter.colorScheme).toBe("dark");
        expect(adapter.themeCssVariables["--tg-bg-color"]).toBe("#101010");
        expect(adapter.themeCssVariables["--tg-text-color"]).toBe("#eeeeee");

        adapter.ready();
        adapter.ready();
        adapter.expand();
        adapter.expand();
        expect(ready).toHaveBeenCalledTimes(1);
        expect(expand).toHaveBeenCalledTimes(1);
    });

    it("applies CSS variables to the document element", () => {
        resetTelegramAdapterGuardsForTests();
        applyThemeCssVariables(document.documentElement, { "--tg-bg-color": "#abcdef" }, "light");
        expect(document.documentElement.dataset.colorScheme).toBe("light");
        expect(document.documentElement.style.getPropertyValue("--tg-bg-color")).toBe("#abcdef");
    });
});
