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
    resetToContacts,
    type NavigationState,
} from "./navigation/stack";
import { AddRuleScreen } from "./screens/AddRuleScreen";
import { ContactDetailScreen } from "./screens/ContactDetailScreen";
import { ContactsScreen } from "./screens/ContactsScreen";
import { DeleteConfirmScreen } from "./screens/DeleteConfirmScreen";
import { DeletedScreen } from "./screens/DeletedScreen";
import { GateScreen } from "./screens/GateScreen";
import { PrivacyScreen } from "./screens/PrivacyScreen";
import {
    applyThemeCssVariables,
    createTelegramAdapter,
    type TelegramAdapter,
} from "./telegram/webapp";

export type AppProps = {
    readonly adapter?: TelegramAdapter;
    readonly fetchImpl?: typeof fetch;
};

function MiniappShell({
    telegram,
    onAccountDeleted,
}: {
    readonly telegram: TelegramAdapter;
    readonly onAccountDeleted: () => void;
}) {
    const me = useMe();
    const [nav, setNav] = useState<NavigationState>(createInitialNavigation());
    const [forceConsentGate, setForceConsentGate] = useState(false);

    useEffect(() => {
        const showBack =
            me.status === "success" &&
            me.data.onboarding_step === "done" &&
            !forceConsentGate &&
            canGoBack(nav);
        if (showBack) {
            telegram.BackButton.show();
        } else {
            telegram.BackButton.hide();
        }
        return telegram.BackButton.onClick(() => {
            setNav((current) => popScreen(current));
        });
    }, [forceConsentGate, me, nav, telegram]);

    if (forceConsentGate) {
        return (
            <GateScreen
                kind="consent"
                telegram={telegram}
                showRightsActions
                onAccountDeleted={onAccountDeleted}
            />
        );
    }

    if (me.status === "loading") {
        return <LoadingView />;
    }

    if (me.status === "error") {
        if (me.error.kind === "unauthorized") {
            return <GateScreen kind="unauthorized" telegram={telegram} />;
        }
        if (me.error.code === "consent_required") {
            return (
                <GateScreen
                    kind="consent"
                    telegram={telegram}
                    showRightsActions
                    onAccountDeleted={onAccountDeleted}
                />
            );
        }
        return <ErrorView message={me.error.message} onRetry={me.refetch} />;
    }

    if (me.data.onboarding_step !== "done") {
        const incomplete = me.data.onboarding_step === "age";
        const showRightsActions = incomplete ? me.data.account_exists : true;
        return (
            <GateScreen
                kind={me.data.onboarding_step === "consent" ? "consent" : "incomplete"}
                telegram={telegram}
                showRightsActions={showRightsActions}
                onAccountDeleted={showRightsActions ? onAccountDeleted : undefined}
            />
        );
    }

    const screen = currentScreen(nav);
    if (screen.name === "contacts") {
        return (
            <ContactsScreen
                activeContactId={me.data.active_contact_id}
                telegram={telegram}
                onOpenContact={(contact) => {
                    setNav((current) =>
                        pushScreen(current, {
                            name: "contactDetail",
                            contactId: contact.id,
                            label: contact.label,
                            relationship: contact.relationship,
                        }),
                    );
                }}
                onOpenPrivacy={() => {
                    setNav((current) => pushScreen(current, { name: "privacy" }));
                }}
                onActivated={me.refetch}
            />
        );
    }
    if (screen.name === "contactDetail") {
        return (
            <ContactDetailScreen
                contactId={screen.contactId}
                label={screen.label}
                relationship={screen.relationship}
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
    if (screen.name === "addRule") {
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
    if (screen.name === "privacy") {
        return (
            <PrivacyScreen
                telegram={telegram}
                onRevoked={() => {
                    setForceConsentGate(true);
                    setNav(resetToContacts());
                }}
                onDeleteRequested={() => {
                    setNav((current) => pushScreen(current, { name: "deleteConfirm" }));
                }}
            />
        );
    }
    return (
        <DeleteConfirmScreen
            telegram={telegram}
            onCancelled={() => {
                setNav((current) => popScreen(current));
            }}
            onDeleted={onAccountDeleted}
        />
    );
}

export function App({ adapter, fetchImpl }: AppProps) {
    const defaultAdapter = useMemo(() => createTelegramAdapter(), []);
    const telegram = adapter ?? defaultAdapter;
    const [sessionKey, setSessionKey] = useState(0);
    const [accountDeleted, setAccountDeleted] = useState(false);

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

    const onAccountDeleted = () => {
        setAccountDeleted(true);
        setSessionKey((current) => current + 1);
    };

    return (
        <div className="app-shell">
            <header className="app-header">
                <h1 className="app-title">{ru.appTitle}</h1>
            </header>
            <main className="app-main">
                {!telegram.isInsideTelegram ? (
                    <p className="app-message">{ru.openFromTelegram}</p>
                ) : accountDeleted ? (
                    <DeletedScreen telegram={telegram} />
                ) : fetchImpl !== undefined ? (
                    <ApiProvider
                        key={sessionKey}
                        initData={telegram.initData}
                        fetchImpl={fetchImpl}
                    >
                        <MiniappShell telegram={telegram} onAccountDeleted={onAccountDeleted} />
                    </ApiProvider>
                ) : (
                    <ApiProvider key={sessionKey} initData={telegram.initData}>
                        <MiniappShell telegram={telegram} onAccountDeleted={onAccountDeleted} />
                    </ApiProvider>
                )}
            </main>
        </div>
    );
}
