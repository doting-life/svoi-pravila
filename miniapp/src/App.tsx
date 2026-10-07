import { useEffect, useMemo, useState } from "react";

import { ApiProvider } from "./api/ApiContext";
import { ErrorView, LoadingView } from "./components/StatusViews";
import { useMe } from "./hooks/useMe";
import type { RuleCategory } from "./hooks/useRules";
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
import { InviteScreen } from "./screens/InviteScreen";
import { OnboardingAgeScreen } from "./screens/OnboardingAgeScreen";
import { OnboardingConsentScreen } from "./screens/OnboardingConsentScreen";
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
    fetchImpl,
}: {
    readonly telegram: TelegramAdapter;
    readonly onAccountDeleted: () => void;
    readonly fetchImpl?: typeof fetch;
}) {
    const me = useMe();
    const [nav, setNav] = useState<NavigationState>(createInitialNavigation());
    const [forceConsentGate, setForceConsentGate] = useState(false);
    const [inviteDismissed, setInviteDismissed] = useState(false);

    const invitePending =
        !inviteDismissed && telegram.startParam !== null && telegram.startParam.startsWith("inv_");

    useEffect(() => {
        const showBack =
            me.status === "success" &&
            me.data.onboarding_step === "done" &&
            !forceConsentGate &&
            !invitePending &&
            canGoBack(nav);
        if (showBack) {
            telegram.BackButton.show();
        } else {
            telegram.BackButton.hide();
        }
        return telegram.BackButton.onClick(() => {
            setNav((current) => popScreen(current));
        });
    }, [forceConsentGate, invitePending, me, nav, telegram]);

    if (me.status === "loading") {
        return <LoadingView />;
    }

    if (me.status === "error") {
        if (me.error.kind === "unauthorized") {
            return <GateScreen kind="unauthorized" telegram={telegram} />;
        }
        if (me.error.code === "consent_required" || forceConsentGate) {
            return (
                <OnboardingConsentScreen
                    telegram={telegram}
                    me={{
                        onboarding_step: "consent",
                        consent_kind: "personal_data",
                        consent_version: null,
                        active_contact_id: null,
                        account_exists: true,
                        max_contacts: 20,
                        max_open_rules: 50,
                        display_timezone: "Europe/Moscow",
                    }}
                    onAdvanced={() => {
                        setForceConsentGate(false);
                        me.refetch();
                    }}
                    onAccountDeleted={onAccountDeleted}
                />
            );
        }
        return <ErrorView message={me.error.message} onRetry={me.refetch} />;
    }

    if (forceConsentGate || me.data.onboarding_step === "consent") {
        return (
            <OnboardingConsentScreen
                telegram={telegram}
                me={me.data}
                onAdvanced={() => {
                    setForceConsentGate(false);
                    me.refetch();
                }}
                onAccountDeleted={onAccountDeleted}
            />
        );
    }

    if (me.data.onboarding_step === "age") {
        return (
            <OnboardingAgeScreen
                telegram={telegram}
                onConfirmed={() => {
                    me.refetch();
                }}
            />
        );
    }

    if (invitePending) {
        return (
            <InviteScreen
                telegram={telegram}
                displayTimezone={me.data.display_timezone}
                onAccepted={() => {
                    setInviteDismissed(true);
                    me.refetch();
                    setNav(resetToContacts());
                }}
                onDismiss={() => {
                    setInviteDismissed(true);
                }}
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
