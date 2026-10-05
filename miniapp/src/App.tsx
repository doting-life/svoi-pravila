import { useEffect, useMemo } from "react";

import { ru } from "./localization/ru";
import {
    applyThemeCssVariables,
    createTelegramAdapter,
    type TelegramAdapter,
} from "./telegram/webapp";

export type AppProps = {
    readonly adapter?: TelegramAdapter;
};

export function App({ adapter }: AppProps) {
    const defaultAdapter = useMemo(() => createTelegramAdapter(), []);
    const telegram = adapter ?? defaultAdapter;

    useEffect(() => {
        applyThemeCssVariables(
            document.documentElement,
            telegram.themeCssVariables,
            telegram.colorScheme,
        );
        if (telegram.isInsideTelegram) {
            telegram.ready();
            telegram.expand();
        }
    }, [telegram]);

    return (
        <div className="app-shell">
            <header className="app-header">
                <h1 className="app-title">{ru.appTitle}</h1>
            </header>
            <main className="app-main">
                {telegram.isInsideTelegram ? (
                    <p className="app-placeholder">{ru.placeholder}</p>
                ) : (
                    <p className="app-message">{ru.openFromTelegram}</p>
                )}
            </main>
        </div>
    );
}
