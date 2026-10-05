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
import { DecodeScreen } from "./screens/DecodeScreen";
import { DeleteConfirmScreen } from "./screens/DeleteConfirmScreen";
import { DeletedScreen } from "./screens/DeletedScreen";
import { GateScreen } from "./screens/GateScreen";
import { PrivacyScreen } from "./screens/PrivacyScreen";
import type { RuleCategory } from "./hooks/useRules";
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
    fetchImpl,
}: {
    readonly telegram: TelegramAdapter;
    readonly onAccountDeleted: () => void;
    readonly fetchImpl?: typeof fetch;
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
        if (showRightsActions) {
            return (
                <GateScreen
                    kind={me.data.onboarding_step === "consent" ? "consent" : "incomplete"}
                    telegram={telegram}
                    showRightsActions
                    onAccountDeleted={onAccountDeleted}
                />
            );
        }
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
                onOpenContact={(contact) => {
                    setNav((current) =>
                        pushScreen(current, {
                            name: "contactDetail",
                            contactId: contact.id,
                            label: contact.label,
                            relationship: contact.relationship,
                            paired: contact.paired,
                        }),
                    );
                }}
                onOpenPrivacy={() => {
                    setNav((current) => pushScreen(current, { name: "privacy" }));
                }}
                onOpenDecode={() => {
                    setNav((current) => pushScreen(current, { name: "decode" }));
                }}
                onActivated={me.refetch}
            />
        );
    }
    if (screen.name === "decode") {
        return (
            <DecodeScreen
                telegram={telegram}
                displayTimezone={me.data.display_timezone}
                activeContactId={me.data.active_contact_id ?? null}
                {...(fetchImpl !== undefined ? { fetchImpl } : {})}
                onEditSuggestion={(category, text) => {
                    const contactId = me.data.active_contact_id;
                    if (contactId == null) {
                        return;
                    }
                    setNav((current) =>
                        pushScreen(current, {
                            name: "addRule",
                            contactId,
                            paired: false,
                            initialCategory: category,
                            initialText: text,
                        }),
                    );
                }}
            />
        );
    }
    if (screen.name === "contactDetail") {
        return (
            <ContactDetailScreen
                contactId={screen.contactId}
                label={screen.label}
                relationship={screen.relationship}
                paired={screen.paired}
                displayTimezone={me.data.display_timezone}
                telegram={telegram}
                onAddRule={() => {
                    setNav((current) =>
                        pushScreen(current, {
                            name: "addRule",
                            contactId: screen.contactId,
                            paired: screen.paired,
                        }),
                    );
                }}
                onRulesChanged={me.refetch}
                onPairingChanged={(paired) => {
                    setNav((current) => {
                        const top = currentScreen(current);
                        if (top.name !== "contactDetail") {
                            return current;
                        }
                        return {
                            stack: [...current.stack.slice(0, -1), { ...top, paired }],
                        };
                    });
                }}
            />
        );
    }
    if (screen.name === "addRule") {
        const initialCategory =
            screen.initialCategory !== undefined
                ? (screen.initialCategory as RuleCategory)
                : undefined;
        return (
            <AddRuleScreen
                contactId={screen.contactId}
                paired={screen.paired}
                telegram={telegram}
                {...(initialCategory !== undefined ? { initialCategory } : {})}
                {...(screen.initialText !== undefined ? { initialText: screen.initialText } : {})}
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
                        <MiniappShell
                            telegram={telegram}
                            onAccountDeleted={onAccountDeleted}
                            fetchImpl={fetchImpl}
                        />
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
