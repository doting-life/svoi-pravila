import { describe, expect, it, vi } from "vitest";

import {
    applyColorScheme,
    createTelegramAdapter,
    hapticImpact,
    hapticSelection,
    readClipboardText,
    resetTelegramAdapterGuardsForTests,
} from "./webapp";

function installMockWebApp(
    initData: string,
    options: { startParam?: string; withDownloadFile?: boolean; platform?: string } = {},
): {
    ready: ReturnType<typeof vi.fn>;
    expand: ReturnType<typeof vi.fn>;
    close: ReturnType<typeof vi.fn>;
    showConfirm: ReturnType<typeof vi.fn>;
    downloadFile: ReturnType<typeof vi.fn>;
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
    const downloadFile = vi.fn(
        (_params: { url: string; file_name: string }, callback?: (accepted: boolean) => void) => {
            callback?.(true);
        },
    );
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
                platform: options.platform ?? (initData ? "ios" : "unknown"),
                initDataUnsafe:
                    options.startParam !== undefined
                        ? { start_param: options.startParam }
                        : undefined,
                colorScheme: "dark",
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
                ...(options.withDownloadFile === false ? {} : { downloadFile }),
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
        downloadFile,
        notificationOccurred,
        backShow,
        backHide,
        onEvent,
        offEvent,
    };
}

describe("createTelegramAdapter", () => {
    it("detects inline_compose from platform and handoff query", () => {
        window.history.replaceState(null, "", "/?view=compose&h=h_abc");
        installMockWebApp("", { platform: "ios" });
        const adapter = createTelegramAdapter();
        expect(adapter.launch.kind).toBe("inline_compose");
        expect(adapter.launch).toEqual({ kind: "inline_compose", handoffToken: "h_abc" });
        adapter.ready();
        adapter.expand();
        window.history.replaceState(null, "", "/");
    });

    it("reports outside Telegram when initData is empty", async () => {
        installMockWebApp("");
        const adapter = createTelegramAdapter();
        expect(adapter.launch.kind).toBe("outside");
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
        const stopTheme = adapter.onColorSchemeChanged(() => undefined);
        expect(stopTheme).toEqual(expect.any(Function));
        stopTheme();
        adapter.BackButton.show();
        adapter.BackButton.hide();
        adapter.BackButton.onClick(() => undefined)();
        adapter.hapticNotification("error");
        adapter.close();
    });

    it("treats missing location as empty search params", () => {
        window.history.replaceState(null, "", "/?view=compose&h=h_loc");
        installMockWebApp("", { platform: "ios" });
        const saved = globalThis.location;
        Object.defineProperty(globalThis, "location", {
            configurable: true,
            value: undefined,
        });
        try {
            const adapter = createTelegramAdapter();
            expect(adapter.launch).toEqual({ kind: "outside" });
        } finally {
            Object.defineProperty(globalThis, "location", {
                configurable: true,
                value: saved,
            });
            window.history.replaceState(null, "", "/");
        }
    });

    it("handles BackButton without offClick cleanup targets", () => {
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "x",
                    colorScheme: "light",
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

    it("reads the colour scheme and calls ready/expand once inside Telegram", () => {
        const { ready, expand } = installMockWebApp("query_id=1&user=%7B%7D");
        const adapter = createTelegramAdapter();
        expect(adapter.launch.kind).toBe("identified");
        expect(adapter.colorScheme).toBe("dark");

        adapter.ready();
        adapter.ready();
        adapter.expand();
        adapter.expand();
        expect(ready).toHaveBeenCalledTimes(1);
        expect(expand).toHaveBeenCalledTimes(1);
    });

    it("resets ready/expand guards for tests", () => {
        const { ready, expand } = installMockWebApp("query_id=1");
        const adapter = createTelegramAdapter();
        adapter.ready();
        adapter.expand();
        expect(ready).toHaveBeenCalledTimes(1);
        resetTelegramAdapterGuardsForTests();
        adapter.ready();
        adapter.expand();
        expect(ready).toHaveBeenCalledTimes(2);
        expect(expand).toHaveBeenCalledTimes(2);
    });

    it("falls back to light colour scheme when WebApp reports light", () => {
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "query_id=1",
                    platform: "ios",
                    colorScheme: "light",
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
        expect(adapter.colorScheme).toBe("light");
    });

    it("reads startParam and downloadFile from WebApp", async () => {
        const mocks = installMockWebApp("query_id=1", { startParam: "inv_abc" });
        const adapter = createTelegramAdapter();
        expect(adapter.startParam).toBe("inv_abc");
        await expect(
            adapter.downloadFile("https://example.test/f.json", "export.json"),
        ).resolves.toBe(true);
        expect(mocks.downloadFile).toHaveBeenCalledWith(
            { url: "https://example.test/f.json", file_name: "export.json" },
            expect.any(Function),
        );
    });

    it("returns false from downloadFile when the method is missing", async () => {
        installMockWebApp("query_id=1", { withDownloadFile: false });
        const adapter = createTelegramAdapter();
        await expect(adapter.downloadFile("https://example.test/f.json", "x.json")).resolves.toBe(
            false,
        );
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

    it("marks the document with the colour scheme", () => {
        applyColorScheme(document.documentElement, "dark");
        expect(document.documentElement.dataset.colorScheme).toBe("dark");
        applyColorScheme(document.documentElement, "light");
        expect(document.documentElement.dataset.colorScheme).toBe("light");
    });

    it("reports the current scheme on themeChanged", () => {
        const mocks = installMockWebApp("query_id=1");
        const adapter = createTelegramAdapter();
        const schemes: string[] = [];
        adapter.onColorSchemeChanged((scheme) => {
            schemes.push(scheme);
        });
        const handler = mocks.onEvent.mock.calls[0]?.[1] as () => void;
        handler();
        expect(schemes).toEqual(["dark"]);
    });

    it("paints the Telegram chrome with one colour", () => {
        const setHeaderColor = vi.fn();
        const setBackgroundColor = vi.fn();
        const setBottomBarColor = vi.fn();
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "query_id=1",
                    colorScheme: "light",
                    setHeaderColor,
                    setBackgroundColor,
                    setBottomBarColor,
                },
            },
        });
        createTelegramAdapter().applyChromeColor("#f5f0e8");
        expect(setHeaderColor).toHaveBeenCalledWith("#f5f0e8");
        expect(setBackgroundColor).toHaveBeenCalledWith("#f5f0e8");
        expect(setBottomBarColor).toHaveBeenCalledWith("#f5f0e8");
    });

    it("tolerates a WebApp without chrome colour or haptic support", () => {
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: { WebApp: { initData: "query_id=1", colorScheme: "light" } },
        });
        createTelegramAdapter().applyChromeColor("#f5f0e8");
        hapticImpact("light");
        hapticSelection();
    });

    it("forwards impact and selection haptics", () => {
        const impactOccurred = vi.fn();
        const selectionChanged = vi.fn();
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "query_id=1",
                    colorScheme: "light",
                    HapticFeedback: { impactOccurred, selectionChanged },
                },
            },
        });
        hapticImpact("light");
        hapticSelection();
        expect(impactOccurred).toHaveBeenCalledWith("light");
        expect(selectionChanged).toHaveBeenCalledTimes(1);
    });

    it("forwards switchInlineQuery when the WebApp method exists", () => {
        const switchInlineQuery = vi.fn();
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "query_id=1",
                    colorScheme: "light",
                    switchInlineQuery,
                    ready: vi.fn(),
                    expand: vi.fn(),
                    close: vi.fn(),
                    showConfirm: vi.fn(),
                    onEvent: vi.fn(),
                    offEvent: vi.fn(),
                    HapticFeedback: { notificationOccurred: vi.fn() },
                },
            },
        });
        const adapter = createTelegramAdapter();
        adapter.switchInlineQuery("token", ["users", "groups", "channels"]);
        expect(switchInlineQuery).toHaveBeenCalledWith("token", ["users", "groups", "channels"]);
    });

    it("forwards openTelegramLink and copyText", async () => {
        const openTelegramLink = vi.fn();
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { writeText: vi.fn(() => Promise.resolve()) },
        });
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "query_id=1",
                    colorScheme: "light",
                    openTelegramLink,
                    ready: vi.fn(),
                    expand: vi.fn(),
                    close: vi.fn(),
                    showConfirm: vi.fn(),
                    onEvent: vi.fn(),
                    offEvent: vi.fn(),
                    HapticFeedback: { notificationOccurred: vi.fn() },
                },
            },
        });
        const adapter = createTelegramAdapter();
        adapter.openTelegramLink("https://t.me/share/url?url=x");
        expect(openTelegramLink).toHaveBeenCalledWith("https://t.me/share/url?url=x");
        await expect(adapter.copyText("hello")).resolves.toBe(true);
    });

    it("falls back when openTelegramLink and clipboard are unavailable", async () => {
        const open = vi.spyOn(window, "open").mockImplementation(() => null);
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: undefined,
        });
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: undefined,
        });
        const adapter = createTelegramAdapter();
        adapter.openTelegramLink("https://t.me/share/url?url=x");
        expect(open).toHaveBeenCalled();
        await expect(adapter.copyText("hello")).resolves.toBe(false);
        open.mockRestore();
    });

    it("no-ops switchInlineQuery when WebApp is missing the method", () => {
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: undefined,
        });
        const adapter = createTelegramAdapter();
        expect(() => {
            adapter.switchInlineQuery("token", ["users"]);
        }).not.toThrow();
    });

    it("invokes themeChanged callback with the current scheme", () => {
        const mocks = installMockWebApp("query_id=1");
        const adapter = createTelegramAdapter();
        const seen: string[] = [];
        const stop = adapter.onColorSchemeChanged((scheme) => {
            seen.push(scheme);
        });
        const themeCall = mocks.onEvent.mock.calls.find((call) => call[0] === "themeChanged");
        expect(themeCall).toBeDefined();
        const handler: unknown = themeCall?.[1];
        expect(typeof handler).toBe("function");
        if (typeof handler !== "function") {
            throw new Error("missing themeChanged handler");
        }
        const themeChanged = handler as () => void;
        themeChanged();
        expect(seen).toEqual(["dark"]);
        stop();
        expect(mocks.offEvent).toHaveBeenCalledWith("themeChanged", themeChanged);
    });

    it("has no cloud storage when the WebApp does not provide it", () => {
        installMockWebApp("query_id=1");
        expect(createTelegramAdapter().cloudStorage).toBeNull();
    });

    it("reads and writes cloud storage items", async () => {
        installMockWebApp("query_id=1");
        const items = new Map<string, string>();
        const failing = new Set<string>();
        Object.defineProperty(window.Telegram?.WebApp ?? {}, "CloudStorage", {
            configurable: true,
            value: {
                getItem: (
                    key: string,
                    callback: (error: string | null, value?: string) => void,
                ) => {
                    if (failing.has(key)) {
                        callback("STORAGE_ERROR");
                        return;
                    }
                    callback(null, items.get(key));
                },
                setItem: (
                    key: string,
                    value: string,
                    callback?: (error: string | null, stored?: boolean) => void,
                ) => {
                    if (failing.has(key)) {
                        callback?.("STORAGE_ERROR");
                        return;
                    }
                    items.set(key, value);
                    callback?.(null, key !== "unstored");
                },
            },
        });
        const storage = createTelegramAdapter().cloudStorage;
        expect(storage).not.toBeNull();
        await expect(storage?.getItem("missing")).resolves.toBe("");
        await expect(storage?.setItem("k", "1")).resolves.toBe(true);
        await expect(storage?.getItem("k")).resolves.toBe("1");
        await expect(storage?.setItem("unstored", "1")).resolves.toBe(false);
        failing.add("bad");
        await expect(storage?.getItem("bad")).rejects.toThrow("STORAGE_ERROR");
        await expect(storage?.setItem("bad", "1")).resolves.toBe(false);
    });
});

describe("readClipboardText", () => {
    it("returns the clipboard text", async () => {
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { readText: () => Promise.resolve("из буфера") },
        });
        await expect(readClipboardText()).resolves.toBe("из буфера");
    });

    it("returns null when the clipboard cannot be read", async () => {
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { readText: () => Promise.reject(new Error("denied")) },
        });
        await expect(readClipboardText()).resolves.toBeNull();
    });
});
