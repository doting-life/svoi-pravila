import { useEffect, useMemo, useState } from "react";

import { ApiProvider } from "./api/ApiContext";
import { ErrorView, LoadingView } from "./components/StatusViews";
import { useMe } from "./hooks/useMe";
import { ru } from "./localization/ru";
import {
    canGoBack,
    createInitialNavigation,
    currentScreen,
    popScreen,
    pushScreen,
    type NavigationState,
} from "./navigation/stack";
import { AddRuleScreen } from "./screens/AddRuleScreen";
import { ContactDetailScreen } from "./screens/ContactDetailScreen";
import { ContactsScreen } from "./screens/ContactsScreen";
import { GateScreen } from "./screens/GateScreen";
import {
    applyThemeCssVariables,
    createTelegramAdapter,
    type TelegramAdapter,
} from "./telegram/webapp";

export type AppProps = {
    readonly adapter?: TelegramAdapter;
    readonly fetchImpl?: typeof fetch;
};

function MiniappShell({ telegram }: { readonly telegram: TelegramAdapter }) {
    const me = useMe();
    const [nav, setNav] = useState<NavigationState>(createInitialNavigation());

    useEffect(() => {
        const showBack =
            me.status === "success" && me.data.onboarding_step === "done" && canGoBack(nav);
        if (showBack) {
            telegram.BackButton.show();
        } else {
            telegram.BackButton.hide();
        }
        return telegram.BackButton.onClick(() => {
            setNav((current) => popScreen(current));
        });
    }, [me, nav, telegram]);

    if (me.status === "loading") {
        return <LoadingView />;
    }

    if (me.status === "error") {
        if (me.error.kind === "unauthorized") {
            return <GateScreen kind="unauthorized" telegram={telegram} />;
        }
        if (me.error.code === "consent_required") {
            return <GateScreen kind="consent" telegram={telegram} />;
        }
        return <ErrorView message={me.error.message} onRetry={me.refetch} />;
    }

    if (me.data.onboarding_step !== "done") {
        return (
            <GateScreen
                kind={me.data.onboarding_step === "consent" ? "consent" : "incomplete"}
                telegram={telegram}
            />
        );
    }

    const screen = currentScreen(nav);
    if (screen.name === "contacts") {
        return (
            <ContactsScreen
                activeContactId={me.data.active_contact_id}
                telegram={telegram}
                onOpenContact={(contactId) => {
                    setNav((current) => pushScreen(current, { name: "contactDetail", contactId }));
                }}
                onActivated={me.refetch}
            />
        );
    }
    if (screen.name === "contactDetail") {
        return (
            <ContactDetailScreen
                contactId={screen.contactId}
                displayTimezone={me.data.display_timezone}
                telegram={telegram}
                onAddRule={() => {
                    setNav((current) =>
                        pushScreen(current, { name: "addRule", contactId: screen.contactId }),
                    );
                }}
                onRulesChanged={me.refetch}
            />
        );
    }
    return (
        <AddRuleScreen
            contactId={screen.contactId}
            telegram={telegram}
            onCreated={() => {
                setNav((current) => popScreen(current));
            }}
        />
    );
}

export function App({ adapter, fetchImpl }: AppProps) {
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
        return telegram.onColorSchemeChanged((scheme) => {
            applyThemeCssVariables(document.documentElement, telegram.themeCssVariables, scheme);
        });
    }, [telegram]);

    return (
        <div className="app-shell">
            <header className="app-header">
                <h1 className="app-title">{ru.appTitle}</h1>
            </header>
            <main className="app-main">
                {!telegram.isInsideTelegram ? (
                    <p className="app-message">{ru.openFromTelegram}</p>
                ) : fetchImpl !== undefined ? (
                    <ApiProvider initData={telegram.initData} fetchImpl={fetchImpl}>
                        <MiniappShell telegram={telegram} />
                    </ApiProvider>
                ) : (
                    <ApiProvider initData={telegram.initData}>
                        <MiniappShell telegram={telegram} />
                    </ApiProvider>
                )}
            </main>
        </div>
    );
}
