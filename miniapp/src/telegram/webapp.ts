export type ColorScheme = "light" | "dark";

export type HapticNotificationType = "error" | "success" | "warning";

export type HapticImpactStyle = "light" | "medium" | "heavy" | "rigid" | "soft";

export type TelegramBackButtonControls = {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => () => void;
};

export type InlineQueryChatType = "users" | "bots" | "groups" | "channels";

export type TelegramAdapter = {
    readonly initData: string;
    readonly startParam: string | null;
    readonly colorScheme: ColorScheme;
    readonly isInsideTelegram: boolean;
    readonly BackButton: TelegramBackButtonControls;
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
    return webApp?.colorScheme === "dark" ? "dark" : "light";
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

/** Build a typed adapter over `window.Telegram.WebApp`. Safe outside Telegram. */
export function createTelegramAdapter(): TelegramAdapter {
    const webApp = readWebApp();
    const initData = webApp?.initData ?? "";
    const isInsideTelegram = initData.length > 0;
    const rawStart = webApp?.initDataUnsafe?.start_param;
    const startParam = typeof rawStart === "string" && rawStart.length > 0 ? rawStart : null;

    return {
        initData,
        startParam,
        get colorScheme() {
            return readColorScheme(readWebApp());
        },
        isInsideTelegram,
        BackButton: createBackButtonControls(webApp),
        ready: () => {
            if (!isInsideTelegram || readyCalled) {
                return;
            }
            webApp?.ready();
            readyCalled = true;
        },
        expand: () => {
            if (!isInsideTelegram || expandCalled) {
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
            const handler = () => {
                callback(readColorScheme(readWebApp()));
            };
            current.onEvent("themeChanged", handler);
            return () => {
                const latest = readWebApp();
                latest?.offEvent?.("themeChanged", handler);
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
