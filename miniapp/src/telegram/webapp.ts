export type ColorScheme = "light" | "dark";

export type ThemeCssVariables = Readonly<Record<`--tg-${string}`, string>>;

export type HapticNotificationType = "error" | "success" | "warning";

export type TelegramBackButtonControls = {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => () => void;
};

export type InlineQueryChatType = "users" | "bots" | "groups" | "channels";

export type TelegramAdapter = {
    readonly initData: string;
    readonly colorScheme: ColorScheme;
    readonly themeCssVariables: ThemeCssVariables;
    readonly isInsideTelegram: boolean;
    readonly BackButton: TelegramBackButtonControls;
    ready: () => void;
    expand: () => void;
    close: () => void;
    showConfirm: (message: string) => Promise<boolean>;
    openTelegramLink: (url: string) => void;
    copyText: (text: string) => Promise<boolean>;
    hapticNotification: (type: HapticNotificationType) => void;
    switchInlineQuery: (query: string, chooseChatTypes: readonly InlineQueryChatType[]) => void;
    onColorSchemeChanged: (callback: (scheme: ColorScheme) => void) => () => void;
};

const THEME_PARAM_TO_CSS: ReadonlyArray<readonly [keyof TelegramThemeParams, `--tg-${string}`]> = [
    ["bg_color", "--tg-bg-color"],
    ["text_color", "--tg-text-color"],
    ["hint_color", "--tg-hint-color"],
    ["link_color", "--tg-link-color"],
    ["button_color", "--tg-button-color"],
    ["button_text_color", "--tg-button-text-color"],
    ["secondary_bg_color", "--tg-secondary-bg-color"],
    ["header_bg_color", "--tg-header-bg-color"],
    ["accent_text_color", "--tg-accent-text-color"],
    ["section_bg_color", "--tg-section-bg-color"],
    ["section_header_text_color", "--tg-section-header-text-color"],
    ["subtitle_text_color", "--tg-subtitle-text-color"],
    ["destructive_text_color", "--tg-destructive-text-color"],
];

function mapThemeParams(themeParams: TelegramThemeParams): ThemeCssVariables {
    const vars: Record<`--tg-${string}`, string> = {};
    for (const [key, cssVar] of THEME_PARAM_TO_CSS) {
        const value = themeParams[key];
        if (typeof value === "string" && value.length > 0) {
            vars[cssVar] = value;
        }
    }
    return vars;
}

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

    return {
        initData,
        get colorScheme() {
            return readColorScheme(readWebApp());
        },
        get themeCssVariables() {
            return mapThemeParams(readWebApp()?.themeParams ?? {});
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

export function applyThemeCssVariables(
    target: HTMLElement,
    variables: ThemeCssVariables,
    colorScheme: ColorScheme,
): void {
    target.dataset.colorScheme = colorScheme;
    for (const [name, value] of Object.entries(variables)) {
        target.style.setProperty(name, value);
    }
}
