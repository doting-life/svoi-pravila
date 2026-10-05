import { describe, expect, it, vi } from "vitest";

import {
    applyThemeCssVariables,
    createTelegramAdapter,
    resetTelegramAdapterGuardsForTests,
} from "./webapp";

function installMockWebApp(initData: string): {
    ready: ReturnType<typeof vi.fn>;
    expand: ReturnType<typeof vi.fn>;
    close: ReturnType<typeof vi.fn>;
    showConfirm: ReturnType<typeof vi.fn>;
    notificationOccurred: ReturnType<typeof vi.fn>;
    backShow: ReturnType<typeof vi.fn>;
    backHide: ReturnType<typeof vi.fn>;
    onEvent: ReturnType<typeof vi.fn>;
    offEvent: ReturnType<typeof vi.fn>;
} {
    const ready = vi.fn();
    const expand = vi.fn();
    const close = vi.fn();
    const showConfirm = vi.fn((_message: string, callback?: (ok: boolean) => void) => {
        callback?.(true);
    });
    const notificationOccurred = vi.fn();
    const backShow = vi.fn();
    const backHide = vi.fn();
    const onEvent = vi.fn();
    const offEvent = vi.fn();
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
                BackButton: {
                    show: backShow,
                    hide: backHide,
                    onClick: vi.fn(),
                    offClick: vi.fn(),
                },
                HapticFeedback: {
                    notificationOccurred,
                },
                ready,
                expand,
                close,
                showConfirm,
                onEvent,
                offEvent,
            },
        },
    });
    return {
        ready,
        expand,
        close,
        showConfirm,
        notificationOccurred,
        backShow,
        backHide,
        onEvent,
        offEvent,
    };
}

describe("createTelegramAdapter", () => {
    it("reports outside Telegram when initData is empty", async () => {
        installMockWebApp("");
        const adapter = createTelegramAdapter();
        expect(adapter.isInsideTelegram).toBe(false);
        expect(adapter.initData).toBe("");
        adapter.ready();
        adapter.expand();
        await expect(adapter.showConfirm("x")).resolves.toBe(true);
        const unsub = adapter.onColorSchemeChanged(() => undefined);
        unsub();
        adapter.BackButton.onClick(() => undefined)();
    });

    it("resolves confirm as false when WebApp is missing", async () => {
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: undefined,
        });
        const adapter = createTelegramAdapter();
        await expect(adapter.showConfirm("x")).resolves.toBe(false);
        expect(adapter.onColorSchemeChanged(() => undefined)).toEqual(expect.any(Function));
        adapter.BackButton.show();
        adapter.BackButton.hide();
        adapter.hapticNotification("error");
        adapter.close();
    });

    it("handles BackButton without offClick cleanup targets", () => {
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "x",
                    colorScheme: "light",
                    themeParams: {},
                    BackButton: {
                        show: vi.fn(),
                        hide: vi.fn(),
                        onClick: vi.fn(),
                        offClick: vi.fn(),
                    },
                    HapticFeedback: { notificationOccurred: vi.fn() },
                    ready: vi.fn(),
                    expand: vi.fn(),
                    close: vi.fn(),
                    showConfirm: vi.fn(),
                    onEvent: vi.fn(),
                    offEvent: vi.fn(),
                },
            },
        });
        const adapter = createTelegramAdapter();
        const stop = adapter.BackButton.onClick(() => undefined);
        stop();
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

    it("exposes confirm, haptic, close, back button and theme events", async () => {
        const mocks = installMockWebApp("query_id=1");
        const adapter = createTelegramAdapter();
        adapter.BackButton.show();
        adapter.BackButton.hide();
        expect(mocks.backShow).toHaveBeenCalled();
        expect(mocks.backHide).toHaveBeenCalled();
        await expect(adapter.showConfirm("ok?")).resolves.toBe(true);
        adapter.hapticNotification("success");
        expect(mocks.notificationOccurred).toHaveBeenCalledWith("success");
        adapter.close();
        expect(mocks.close).toHaveBeenCalled();
        const unsub = adapter.onColorSchemeChanged(() => undefined);
        expect(mocks.onEvent).toHaveBeenCalledWith("themeChanged", expect.any(Function));
        unsub();
        expect(mocks.offEvent).toHaveBeenCalled();
    });

    it("applies CSS variables to the document element", () => {
        resetTelegramAdapterGuardsForTests();
        applyThemeCssVariables(document.documentElement, { "--tg-bg-color": "#abcdef" }, "light");
        expect(document.documentElement.dataset.colorScheme).toBe("light");
        expect(document.documentElement.style.getPropertyValue("--tg-bg-color")).toBe("#abcdef");
    });
});
