/// <reference types="vite/client" />

interface TelegramThemeParams {
    readonly bg_color?: string;
    readonly text_color?: string;
    readonly hint_color?: string;
    readonly link_color?: string;
    readonly button_color?: string;
    readonly button_text_color?: string;
    readonly secondary_bg_color?: string;
    readonly header_bg_color?: string;
    readonly accent_text_color?: string;
    readonly section_bg_color?: string;
    readonly section_header_text_color?: string;
    readonly subtitle_text_color?: string;
    readonly destructive_text_color?: string;
}

type TelegramHapticNotificationType = "error" | "success" | "warning";

interface TelegramBackButton {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => void;
    offClick: (callback: () => void) => void;
}

interface TelegramHapticFeedback {
    notificationOccurred: (type: TelegramHapticNotificationType) => void;
}

type TelegramInlineQueryChatType = "users" | "bots" | "groups" | "channels";

interface TelegramWebAppInitDataUnsafe {
    readonly start_param?: string;
}

interface TelegramDownloadFileParams {
    readonly url: string;
    readonly file_name: string;
}

interface TelegramWebApp {
    readonly initData: string;
    readonly initDataUnsafe?: TelegramWebAppInitDataUnsafe;
    readonly colorScheme: "light" | "dark";
    readonly themeParams: TelegramThemeParams;
    readonly BackButton?: TelegramBackButton;
    readonly HapticFeedback?: TelegramHapticFeedback;
    ready: () => void;
    expand: () => void;
    close: () => void;
    showConfirm?: (message: string, callback?: (confirmed: boolean) => void) => void;
    openTelegramLink?: (url: string) => void;
    downloadFile?: (
        params: TelegramDownloadFileParams,
        callback?: (accepted: boolean) => void,
    ) => void;
    switchInlineQuery?: (query: string, chooseChatTypes?: TelegramInlineQueryChatType[]) => void;
    onEvent?: (eventType: string, callback: () => void) => void;
    offEvent?: (eventType: string, callback: () => void) => void;
}

interface TelegramNamespace {
    readonly WebApp: TelegramWebApp;
}

interface Window {
    readonly Telegram?: TelegramNamespace;
}
