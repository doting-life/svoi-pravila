/// <reference types="vite/client" />

type TelegramHapticNotificationType = "error" | "success" | "warning";

interface TelegramBackButton {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => void;
    offClick: (callback: () => void) => void;
}

type TelegramHapticImpactStyle = "light" | "medium" | "heavy" | "rigid" | "soft";

interface TelegramHapticFeedback {
    notificationOccurred: (type: TelegramHapticNotificationType) => void;
    impactOccurred?: (style: TelegramHapticImpactStyle) => void;
    selectionChanged?: () => void;
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
    readonly BackButton?: TelegramBackButton;
    readonly HapticFeedback?: TelegramHapticFeedback;
    ready: () => void;
    expand: () => void;
    close: () => void;
    setHeaderColor?: (color: string) => void;
    setBackgroundColor?: (color: string) => void;
    setBottomBarColor?: (color: string) => void;
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
