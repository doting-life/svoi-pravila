import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiProvider } from "./api/ApiContext";
import { EmptyState } from "./components/EmptyState";
import { LogoBubbles } from "./components/icons";
import { ErrorView, LoadingView } from "./components/StatusViews";
import { TabBar } from "./components/TabBar";
import { useIntro } from "./hooks/useIntro";
import { useMe, type Me } from "./hooks/useMe";
import { usePendingRules } from "./hooks/usePendingRules";
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
    type OverlayScreen,
} from "./navigation/stack";
import { AddRuleScreen } from "./screens/AddRuleScreen";
import { ContactDetailScreen } from "./screens/ContactDetailScreen";
import { CrisisScreen } from "./screens/CrisisScreen";
import { ComposeScreen } from "./screens/ComposeScreen";
import { DecodeScreen } from "./screens/DecodeScreen";
import { DeleteConfirmScreen } from "./screens/DeleteConfirmScreen";
import { DeletedScreen } from "./screens/DeletedScreen";
import { GateScreen } from "./screens/GateScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { InlineComposeApp } from "./screens/InlineComposeApp";
import { InviteScreen } from "./screens/InviteScreen";
import { LimitScreen } from "./screens/LimitScreen";
import { OnboardingAgeScreen } from "./screens/OnboardingAgeScreen";
import { OnboardingConsentScreen } from "./screens/OnboardingConsentScreen";
import { OnboardingIntroScreen } from "./screens/OnboardingIntroScreen";
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
    const pendingRules = usePendingRules();
    const [focusPending] = useState(() => {
        if (typeof window === "undefined") {
            return false;
        }
        return new URLSearchParams(window.location.search).get("view") === "pending";
    });
    const [nav, setNav] = useState<NavigationState>(() => {
        const stack: OverlayScreen[] = [];
        if (telegram.startParam?.startsWith("inv_") === true) {
            stack.push({ name: "invite" });
        }
        return createInitialNavigation(stack, focusPending ? "people" : "home");
    });
    const [forceConsentGate, setForceConsentGate] = useState(false);
    const [local, setLocal] = useState<LocalOverrides | null>(null);
    const intro = useIntro(telegram);

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
                        consentsRevoked
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
                    consentsRevoked={data.consents_revoked}
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
    const decodeRemaining = Math.max(0, data.decode_remaining - (overrides?.decodesUsed ?? 0));

    const recordDecodeUsed = () => {
        setLocal({
            source: data,
            decodesUsed: (overrides?.decodesUsed ?? 0) + 1,
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
    const pendingCount = pendingRules.status === "success" ? pendingRules.data.length : 0;
    const tabs = isRoot ? (
        <TabBar
            active={nav.root}
            badges={{ people: pendingCount }}
            onChange={(tab) => {
                setNav(selectRoot(tab));
            }}
        />
    ) : undefined;

    switch (screen.name) {
        case "home":
            if (intro.state === "loading") {
                return (
                    <Page>
                        <LoadingView />
                    </Page>
                );
            }
            if (intro.state === "show") {
                return (
                    <Page>
                        <OnboardingIntroScreen
                            botUsername={data.bot_username}
                            onDismiss={intro.markSeen}
                            onTryExample={() => {
                                intro.markSeen();
                                push({
                                    name: "compose",
                                    mode: "check",
                                    draft: ru.introExampleDraft,
                                    contactId: null,
                                    title: ru.introExampleTitle,
                                });
                            }}
                        />
                    </Page>
                );
            }
            return (
                <Page tabs={tabs}>
                    <HomeScreen
                        textMax={data.incoming_text_max_chars}
                        onSubmit={({ mode, draft, contactId }) => {
                            if (mode === "decode") {
                                push({ name: "decode", draft, contactId });
                                return;
                            }
                            push({ name: "compose", mode, draft, contactId });
                        }}
                    />
                </Page>
            );
        case "people":
            return (
                <Page tabs={tabs}>
                    <PeopleScreen
                        telegram={telegram}
                        pendingRules={pendingRules}
                        focusPending={focusPending}
                        pendingByContact={
                            pendingRules.status === "success"
                                ? pendingRules.data.reduce<Record<string, number>>((acc, item) => {
                                      acc[item.contact_id] = (acc[item.contact_id] ?? 0) + 1;
                                      return acc;
                                  }, {})
                                : {}
                        }
                        onOpenContact={(contact) => {
                            push({
                                name: "contactDetail",
                                contactId: contact.id,
                                label: contact.label,
                                relationship: contact.relationship,
                                paired: contact.paired,
                            });
                        }}
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
                        initialText={screen.draft}
                        initialContactId={screen.contactId}
                        decodeRemaining={decodeRemaining}
                        onDecodeUsed={recordDecodeUsed}
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
        case "compose":
            return (
                <Page>
                    <ComposeScreen
                        telegram={telegram}
                        mode={screen.mode}
                        {...(screen.title !== undefined ? { title: screen.title } : {})}
                        draft={screen.draft}
                        contactId={screen.contactId}
                        draftMax={data.incoming_text_max_chars}
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
        if (telegram.launch.kind !== "outside") {
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

    if (telegram.launch.kind === "outside") {
        return (
            <div className="app-shell">
                <Page>
                    <section className="screen screen--centered" aria-labelledby="outside-title">
                        <EmptyState
                            illustration={<LogoBubbles />}
                            title={ru.openFromTelegram}
                            titleId="outside-title"
                        />
                    </section>
                </Page>
            </div>
        );
    }

    if (telegram.launch.kind === "inline_compose") {
        return (
            <div className="app-shell">
                <InlineComposeApp
                    telegram={telegram}
                    handoffToken={telegram.launch.handoffToken}
                    {...(fetchImpl !== undefined ? { fetchImpl } : {})}
                />
            </div>
        );
    }

    const credential = { scheme: "tma" as const, value: telegram.initData };

    return (
        <div className="app-shell">
            {accountDeleted ? (
                <Page>
                    <DeletedScreen telegram={telegram} />
                </Page>
            ) : fetchImpl !== undefined ? (
                <ApiProvider key={sessionKey} credential={credential} fetchImpl={fetchImpl}>
                    <MiniappShell
                        telegram={telegram}
                        onAccountDeleted={onAccountDeleted}
                        fetchImpl={fetchImpl}
                    />
                </ApiProvider>
            ) : (
                <ApiProvider key={sessionKey} credential={credential}>
                    <MiniappShell telegram={telegram} onAccountDeleted={onAccountDeleted} />
                </ApiProvider>
            )}
        </div>
    );
}
