export type ColorScheme = "light" | "dark";

export type HapticNotificationType = "error" | "success" | "warning";

export type HapticImpactStyle = "light" | "medium" | "heavy" | "rigid" | "soft";

export type TelegramBackButtonControls = {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => () => void;
};

export type InlineQueryChatType = "users" | "bots" | "groups" | "channels";

/** How the mini-app was opened (ADR-0013 amendment 1). */
export type Launch =
    | { readonly kind: "outside" }
    | { readonly kind: "identified" }
    | { readonly kind: "inline_compose"; readonly handoffToken: string };

/** Telegram CloudStorage; `getItem` resolves `""` for a missing key and rejects on failure. */
export type TelegramCloudStorageControls = {
    getItem: (key: string) => Promise<string>;
    setItem: (key: string, value: string) => Promise<boolean>;
};

export type TelegramAdapter = {
    readonly initData: string;
    readonly startParam: string | null;
    readonly colorScheme: ColorScheme;
    readonly launch: Launch;
    readonly BackButton: TelegramBackButtonControls;
    /** `null` when the client has no CloudStorage. */
    readonly cloudStorage: TelegramCloudStorageControls | null;
    ready: () => void;
    expand: () => void;
    close: () => void;
    showConfirm: (message: string) => Promise<boolean>;
    openTelegramLink: (url: string) => void;
    downloadFile: (url: string, fileName: string) => Promise<boolean>;
    copyText: (text: string) => Promise<boolean>;
    hapticNotification: (type: HapticNotificationType) => void;
    applyChromeColor: (color: string) => void;
    switchInlineQuery: (query: string, chooseChatTypes: readonly InlineQueryChatType[]) => void;
    onColorSchemeChanged: (callback: (scheme: ColorScheme) => void) => () => void;
};

function readWebApp(): TelegramWebApp | undefined {
    return window.Telegram?.WebApp;
}

function readColorScheme(webApp: TelegramWebApp | undefined): ColorScheme {
    if (webApp !== undefined && webApp.colorScheme === "dark") {
        return "dark";
    }
    return "light";
}

function readSearchParams(): URLSearchParams {
    // jsdom / Node test harnesses can leave `location` unset; DOM typings always define it.
    const loc = globalThis.location as Location | undefined;
    if (loc === undefined) {
        return new URLSearchParams();
    }
    return new URLSearchParams(loc.search);
}

function detectLaunch(webApp: TelegramWebApp | undefined, initData: string): Launch {
    if (initData.length > 0) {
        return { kind: "identified" };
    }
    const params = readSearchParams();
    const view = params.get("view");
    const handoff = params.get("h");
    const platform = webApp?.platform;
    const insideTelegram = platform !== undefined && platform !== "unknown";
    if (insideTelegram && view === "compose" && handoff !== null && handoff.length > 0) {
        return { kind: "inline_compose", handoffToken: handoff };
    }
    return { kind: "outside" };
}

let readyCalled = false;
let expandCalled = false;

function noopBackButton(): TelegramBackButtonControls {
    return {
        show: () => undefined,
        hide: () => undefined,
        onClick: () => () => undefined,
    };
}

function createBackButtonControls(webApp: TelegramWebApp | undefined): TelegramBackButtonControls {
    const back = webApp?.BackButton;
    if (back === undefined) {
        return noopBackButton();
    }
    return {
        show: () => {
            back.show();
        },
        hide: () => {
            back.hide();
        },
        onClick: (callback: () => void) => {
            back.onClick(callback);
            return () => {
                back.offClick(callback);
            };
        },
    };
}

function createCloudStorageControls(
    webApp: TelegramWebApp | undefined,
): TelegramCloudStorageControls | null {
    const storage = webApp?.CloudStorage;
    if (storage === undefined) {
        return null;
    }
    return {
        getItem: (key: string) =>
            new Promise<string>((resolve, reject) => {
                storage.getItem(key, (error, value) => {
                    if (error !== null) {
                        reject(new Error(error));
                        return;
                    }
                    resolve(value ?? "");
                });
            }),
        setItem: (key: string, value: string) =>
            new Promise<boolean>((resolve) => {
                storage.setItem(key, value, (error, stored) => {
                    resolve(error === null && stored === true);
                });
            }),
    };
}

/** Clipboard text, or `null` when the browser refuses to read it. */
export async function readClipboardText(): Promise<string | null> {
    try {
        return await navigator.clipboard.readText();
    } catch {
        return null;
    }
}

/** Build a typed adapter over `window.Telegram.WebApp`. Safe outside Telegram. */
export function createTelegramAdapter(): TelegramAdapter {
    const webApp = readWebApp();
    const initData = webApp?.initData ?? "";
    const launch = detectLaunch(webApp, initData);
    const insideTelegram = launch.kind !== "outside";
    const rawStart = webApp?.initDataUnsafe?.start_param;
    const startParam = typeof rawStart === "string" && rawStart.length > 0 ? rawStart : null;

    return {
        initData,
        startParam,
        get colorScheme() {
            return readColorScheme(readWebApp());
        },
        launch,
        BackButton: createBackButtonControls(webApp),
        cloudStorage: createCloudStorageControls(webApp),
        ready: () => {
            if (!insideTelegram || readyCalled) {
                return;
            }
            webApp?.ready();
            readyCalled = true;
        },
        expand: () => {
            if (!insideTelegram || expandCalled) {
                return;
            }
            webApp?.expand();
            expandCalled = true;
        },
        close: () => {
            webApp?.close();
        },
        showConfirm: (message: string) =>
            new Promise<boolean>((resolve) => {
                const current = readWebApp();
                if (current === undefined || typeof current.showConfirm !== "function") {
                    resolve(false);
                    return;
                }
                current.showConfirm(message, (confirmed) => {
                    resolve(confirmed);
                });
            }),
        openTelegramLink: (url: string) => {
            const current = readWebApp();
            if (current !== undefined && typeof current.openTelegramLink === "function") {
                current.openTelegramLink(url);
                return;
            }
            window.open(url, "_blank", "noopener,noreferrer");
        },
        downloadFile: (url: string, fileName: string) =>
            new Promise<boolean>((resolve) => {
                const current = readWebApp();
                if (current === undefined || typeof current.downloadFile !== "function") {
                    resolve(false);
                    return;
                }
                current.downloadFile({ url, file_name: fileName }, (accepted) => {
                    resolve(accepted);
                });
            }),
        copyText: async (text: string) => {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch {
                return false;
            }
        },
        hapticNotification: (type: HapticNotificationType) => {
            readWebApp()?.HapticFeedback?.notificationOccurred(type);
        },
        applyChromeColor: (color: string) => {
            const current = readWebApp();
            current?.setHeaderColor?.(color);
            current?.setBackgroundColor?.(color);
            current?.setBottomBarColor?.(color);
        },
        switchInlineQuery: (query: string, chooseChatTypes: readonly InlineQueryChatType[]) => {
            const current = readWebApp();
            if (current === undefined || typeof current.switchInlineQuery !== "function") {
                return;
            }
            current.switchInlineQuery(query, [...chooseChatTypes]);
        },
        onColorSchemeChanged: (callback: (scheme: ColorScheme) => void) => {
            const current = readWebApp();
            if (current === undefined || typeof current.onEvent !== "function") {
                return () => undefined;
            }
            function themeChangedHandler(): void {
                callback(readColorScheme(readWebApp()));
            }
            current.onEvent("themeChanged", themeChangedHandler);
            return () => {
                const latest = readWebApp();
                latest?.offEvent?.("themeChanged", themeChangedHandler);
            };
        },
    };
}

/** Test-only: reset one-shot ready/expand guards. */
export function resetTelegramAdapterGuardsForTests(): void {
    readyCalled = false;
    expandCalled = false;
}

export function applyColorScheme(target: HTMLElement, colorScheme: ColorScheme): void {
    target.dataset.colorScheme = colorScheme;
}

/** Light tap feedback for taps on primary controls; no-op outside Telegram. */
export function hapticImpact(style: HapticImpactStyle): void {
    readWebApp()?.HapticFeedback?.impactOccurred?.(style);
}

/** Selection-change feedback for tabs, chips and segmented controls; no-op outside Telegram. */
export function hapticSelection(): void {
    readWebApp()?.HapticFeedback?.selectionChanged?.();
}
