import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiProvider } from "./api/ApiContext";
import { EmptyState } from "./components/EmptyState";
import { LogoBubbles } from "./components/icons";
import { ErrorView, LoadingView } from "./components/StatusViews";
import { TabBar } from "./components/TabBar";
import { useMe, type Me } from "./hooks/useMe";
import type { RuleCategory } from "./hooks/useRules";
import { ru } from "./localization/ru";
import {
    canGoBack,
    createInitialNavigation,
    currentScreen,
    popScreen,
    pushScreen,
    replaceTop,
    selectRoot,
    type NavigationState,
} from "./navigation/stack";
import { AddRuleScreen } from "./screens/AddRuleScreen";
import { ContactDetailScreen } from "./screens/ContactDetailScreen";
import { CrisisScreen } from "./screens/CrisisScreen";
import { DecodeScreen } from "./screens/DecodeScreen";
import { DeleteConfirmScreen } from "./screens/DeleteConfirmScreen";
import { DeletedScreen } from "./screens/DeletedScreen";
import { GateScreen } from "./screens/GateScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { InviteScreen } from "./screens/InviteScreen";
import { LimitScreen } from "./screens/LimitScreen";
import { OnboardingAgeScreen } from "./screens/OnboardingAgeScreen";
import { OnboardingConsentScreen } from "./screens/OnboardingConsentScreen";
import { PeopleScreen } from "./screens/PeopleScreen";
import { PrivacyScreen } from "./screens/PrivacyScreen";
import {
    applyColorScheme,
    createTelegramAdapter,
    type ColorScheme,
    type TelegramAdapter,
} from "./telegram/webapp";

export type AppProps = {
    readonly adapter?: TelegramAdapter;
    readonly fetchImpl?: typeof fetch;
};

type LocalOverrides = {
    readonly source: Me;
    readonly decodesUsed: number;
    readonly activeContactId: string | null | undefined;
};

function Page({ children, tabs }: { readonly children: ReactNode; readonly tabs?: ReactNode }) {
    return (
        <>
            <main className="app-main">{children}</main>
            {tabs}
        </>
    );
}

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
    const [nav, setNav] = useState<NavigationState>(() =>
        createInitialNavigation(
            telegram.startParam?.startsWith("inv_") === true ? [{ name: "invite" }] : [],
        ),
    );
    const [forceConsentGate, setForceConsentGate] = useState(false);
    const [local, setLocal] = useState<LocalOverrides | null>(null);

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

    if (me.status === "loading") {
        return (
            <Page>
                <LoadingView />
            </Page>
        );
    }

    const advanceConsent = () => {
        setForceConsentGate(false);
        me.refetch();
    };

    if (me.status === "error") {
        if (me.error.kind === "unauthorized") {
            return (
                <Page>
                    <GateScreen kind="unauthorized" telegram={telegram} />
                </Page>
            );
        }
        if (me.error.code === "consent_required" || forceConsentGate) {
            return (
                <Page>
                    <OnboardingConsentScreen
                        telegram={telegram}
                        consentKind="personal_data"
                        accountExists
                        botUsername={null}
                        onAdvanced={advanceConsent}
                        onAccountDeleted={onAccountDeleted}
                    />
                </Page>
            );
        }
        return (
            <Page>
                <ErrorView message={me.error.message} onRetry={me.refetch} />
            </Page>
        );
    }

    const data = me.data;

    if (forceConsentGate || data.onboarding_step === "consent") {
        return (
            <Page>
                <OnboardingConsentScreen
                    telegram={telegram}
                    consentKind={data.consent_kind ?? null}
                    accountExists={data.account_exists}
                    botUsername={data.bot_username}
                    onAdvanced={advanceConsent}
                    onAccountDeleted={onAccountDeleted}
                />
            </Page>
        );
    }

    if (data.onboarding_step === "age") {
        return (
            <Page>
                <OnboardingAgeScreen
                    telegram={telegram}
                    onConfirmed={() => {
                        me.refetch();
                    }}
                />
            </Page>
        );
    }

    const overrides = local !== null && local.source === data ? local : null;
    const activeContactId =
        overrides !== null && overrides.activeContactId !== undefined
            ? overrides.activeContactId
            : data.active_contact_id;
    const decodeRemaining = Math.max(0, data.decode_remaining - (overrides?.decodesUsed ?? 0));

    const activateLocally = (contactId: string) => {
        setLocal({
            source: data,
            decodesUsed: overrides?.decodesUsed ?? 0,
            activeContactId: contactId,
        });
    };

    const recordDecodeUsed = () => {
        setLocal({
            source: data,
            decodesUsed: (overrides?.decodesUsed ?? 0) + 1,
            activeContactId: overrides?.activeContactId,
        });
    };

    const push = (screen: Parameters<typeof pushScreen>[1]) => {
        setNav((current) => pushScreen(current, screen));
    };
    const pop = () => {
        setNav((current) => popScreen(current));
    };

    const screen = currentScreen(nav);
    const isRoot = nav.stack.length === 0;
    const tabs = isRoot ? (
        <TabBar
            active={nav.root}
            onChange={(tab) => {
                setNav(selectRoot(tab));
            }}
        />
    ) : undefined;

    switch (screen.name) {
        case "home":
            return (
                <Page tabs={tabs}>
                    <HomeScreen
                        telegram={telegram}
                        activeContactId={activeContactId}
                        botUsername={data.bot_username}
                        onOpenDecode={() => {
                            push({ name: "decode" });
                        }}
                        onOpenPeople={() => {
                            setNav(selectRoot("people"));
                        }}
                        onActivated={activateLocally}
                    />
                </Page>
            );
        case "people":
            return (
                <Page tabs={tabs}>
                    <PeopleScreen
                        activeContactId={activeContactId}
                        telegram={telegram}
                        onOpenContact={(contact) => {
                            push({
                                name: "contactDetail",
                                contactId: contact.id,
                                label: contact.label,
                                relationship: contact.relationship,
                                paired: contact.paired,
                            });
                        }}
                        onActivated={activateLocally}
                    />
                </Page>
            );
        case "privacy":
            return (
                <Page tabs={tabs}>
                    <PrivacyScreen
                        telegram={telegram}
                        onRevoked={() => {
                            setForceConsentGate(true);
                            setNav(createInitialNavigation());
                        }}
                        onDeleteRequested={() => {
                            push({ name: "deleteConfirm" });
                        }}
                    />
                </Page>
            );
        case "decode":
            return (
                <Page>
                    <DecodeScreen
                        telegram={telegram}
                        displayTimezone={data.display_timezone}
                        activeContactId={activeContactId ?? null}
                        decodeRemaining={decodeRemaining}
                        onDecodeUsed={recordDecodeUsed}
                        onActivated={activateLocally}
                        {...(fetchImpl !== undefined ? { fetchImpl } : {})}
                        onEditSuggestion={(contact, category, text) => {
                            push({
                                name: "addRule",
                                contactId: contact.id,
                                label: contact.label,
                                paired: contact.paired,
                                initialCategory: category,
                                initialText: text,
                            });
                        }}
                        onLimit={(kind, message) => {
                            setNav((current) =>
                                replaceTop(current, { name: "limit", kind, message }),
                            );
                        }}
                        onCrisis={(lead, resources) => {
                            setNav((current) =>
                                replaceTop(current, { name: "crisis", lead, resources }),
                            );
                        }}
                    />
                </Page>
            );
        case "limit":
            return (
                <Page>
                    <LimitScreen
                        kind={screen.kind}
                        message={screen.message}
                        onOpenCrisis={() => {
                            push({ name: "crisis", lead: null, resources: [] });
                        }}
                        onClose={pop}
                    />
                </Page>
            );
        case "crisis":
            return (
                <Page>
                    <CrisisScreen lead={screen.lead} resources={screen.resources} onBack={pop} />
                </Page>
            );
        case "contactDetail":
            return (
                <Page>
                    <ContactDetailScreen
                        contactId={screen.contactId}
                        label={screen.label}
                        relationship={screen.relationship}
                        paired={screen.paired}
                        displayTimezone={data.display_timezone}
                        telegram={telegram}
                        onAddRule={() => {
                            push({
                                name: "addRule",
                                contactId: screen.contactId,
                                label: screen.label,
                                paired: screen.paired,
                            });
                        }}
                        onPairingChanged={(paired) => {
                            setNav((current) => replaceTop(current, { ...screen, paired }));
                        }}
                        onRenamed={(label) => {
                            setNav((current) => replaceTop(current, { ...screen, label }));
                        }}
                    />
                </Page>
            );
        case "addRule": {
            const initialCategory =
                screen.initialCategory !== undefined
                    ? (screen.initialCategory as RuleCategory)
                    : undefined;
            return (
                <Page>
                    <AddRuleScreen
                        contactId={screen.contactId}
                        contactLabel={screen.label}
                        paired={screen.paired}
                        telegram={telegram}
                        {...(initialCategory !== undefined ? { initialCategory } : {})}
                        {...(screen.initialText !== undefined
                            ? { initialText: screen.initialText }
                            : {})}
                        onCreated={pop}
                    />
                </Page>
            );
        }
        case "invite":
            return (
                <Page>
                    <InviteScreen
                        telegram={telegram}
                        displayTimezone={data.display_timezone}
                        onAccepted={() => {
                            me.refetch();
                            setNav(selectRoot("people"));
                        }}
                        onDismiss={pop}
                    />
                </Page>
            );
        case "deleteConfirm":
            return (
                <Page>
                    <DeleteConfirmScreen
                        telegram={telegram}
                        onCancelled={pop}
                        onDeleted={onAccountDeleted}
                    />
                </Page>
            );
    }
}

function syncTheme(telegram: TelegramAdapter, scheme: ColorScheme): void {
    const root = document.documentElement;
    applyColorScheme(root, scheme);
    const background = getComputedStyle(root).getPropertyValue("--bg").trim();
    if (background.length > 0) {
        telegram.applyChromeColor(background);
    }
}

export function App({ adapter, fetchImpl }: AppProps) {
    const defaultAdapter = useMemo(() => createTelegramAdapter(), []);
    const telegram = adapter ?? defaultAdapter;
    const [sessionKey, setSessionKey] = useState(0);
    const [accountDeleted, setAccountDeleted] = useState(false);

    useEffect(() => {
        syncTheme(telegram, telegram.colorScheme);
        if (telegram.isInsideTelegram) {
            telegram.ready();
            telegram.expand();
        }
        return telegram.onColorSchemeChanged((scheme) => {
            syncTheme(telegram, scheme);
        });
    }, [telegram]);

    const onAccountDeleted = () => {
        setAccountDeleted(true);
        setSessionKey((current) => current + 1);
    };

    return (
        <div className="app-shell">
            {!telegram.isInsideTelegram ? (
                <Page>
                    <section className="screen screen--centered" aria-labelledby="outside-title">
                        <EmptyState
                            illustration={<LogoBubbles />}
                            title={ru.openFromTelegram}
                            titleId="outside-title"
                        />
                    </section>
                </Page>
            ) : accountDeleted ? (
                <Page>
                    <DeletedScreen telegram={telegram} />
                </Page>
            ) : fetchImpl !== undefined ? (
                <ApiProvider key={sessionKey} initData={telegram.initData} fetchImpl={fetchImpl}>
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
        </div>
    );
}
