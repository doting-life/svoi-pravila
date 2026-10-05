export type ColorScheme = "light" | "dark";

export type ThemeCssVariables = Readonly<Record<`--tg-${string}`, string>>;

export type TelegramAdapter = {
    readonly initData: string;
    readonly colorScheme: ColorScheme;
    readonly themeCssVariables: ThemeCssVariables;
    readonly isInsideTelegram: boolean;
    ready: () => void;
    expand: () => void;
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

let readyCalled = false;
let expandCalled = false;

/** Build a typed adapter over `window.Telegram.WebApp`. Safe outside Telegram. */
export function createTelegramAdapter(): TelegramAdapter {
    const webApp = readWebApp();
    const initData = webApp?.initData ?? "";
    const isInsideTelegram = initData.length > 0;
    const colorScheme: ColorScheme = webApp?.colorScheme === "dark" ? "dark" : "light";
    const themeCssVariables = mapThemeParams(webApp?.themeParams ?? {});

    return {
        initData,
        colorScheme,
        themeCssVariables,
        isInsideTelegram,
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
