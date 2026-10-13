import { useEffect, useRef, useState } from "react";

import { ApiProvider } from "../api/ApiContext";
import { redeemComposeHandoff, type ComposeInlineContext } from "../api/client";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { LogoBubbles } from "../components/icons";
import { ScreenHeader } from "../components/ScreenHeader";
import { Skeleton } from "../components/Skeleton";
import { ru } from "../localization/ru";
import type { LimitKind } from "../navigation/stack";
import type { TelegramAdapter } from "../telegram/webapp";
import { ComposeEditor } from "./ComposeScreen";
import { CrisisScreen } from "./CrisisScreen";
import { LimitScreen } from "./LimitScreen";

export type InlineComposeAppProps = {
    readonly telegram: TelegramAdapter;
    readonly handoffToken: string;
    readonly fetchImpl?: typeof fetch;
};

type Phase =
    | { readonly kind: "loading" }
    | { readonly kind: "error"; readonly message: string }
    | { readonly kind: "ready"; readonly context: ComposeInlineContext }
    | { readonly kind: "limit"; readonly limitKind: LimitKind; readonly message: string | null }
    | {
          readonly kind: "crisis";
          readonly lead: string | null;
          readonly resources: readonly string[];
      };

function messageForRedeemError(code: string | undefined): string {
    switch (code) {
        case "handoff_expired":
            return ru.composeInlineHandoffExpired;
        case "compose_session_expired":
            return ru.composeInlineSessionExpired;
        case "onboarding_required":
            return ru.composeInlineOnboarding;
        case "consent_required":
            return ru.composeInlineConsent;
        case "rate_limited":
            return ru.rateLimited;
        default:
            return ru.errorGeneric;
    }
}

function sliceCodePoints(text: string, max: number): string {
    return Array.from(text).slice(0, max).join("");
}

export function InlineComposeApp({ telegram, handoffToken, fetchImpl }: InlineComposeAppProps) {
    const [phase, setPhase] = useState<Phase>({ kind: "loading" });
    const started = useRef(false);

    useEffect(() => {
        telegram.BackButton.hide();
        return telegram.BackButton.onClick(() => {
            telegram.close();
        });
    }, [telegram]);

    useEffect(() => {
        if (started.current) {
            return;
        }
        started.current = true;
        const run = async () => {
            const result = await redeemComposeHandoff(handoffToken, fetchImpl ?? fetch);
            if ("error" in result) {
                setPhase({
                    kind: "error",
                    message: messageForRedeemError(result.error.code),
                });
                return;
            }
            setPhase({ kind: "ready", context: result.data });
        };
        void run();
    }, [fetchImpl, handoffToken]);

    if (phase.kind === "loading") {
        return (
            <main className="app-main">
                <section className="screen" aria-labelledby="compose-title">
                    <ScreenHeader title={ru.composeTitle} titleId="compose-title" />
                    <div role="status">
                        <span className="visually-hidden">{ru.loading}</span>
                        <Skeleton shape="block" />
                        <Skeleton shape="line" />
                    </div>
                </section>
            </main>
        );
    }

    if (phase.kind === "error") {
        return (
            <main className="app-main">
                <section className="screen screen--centered" aria-labelledby="inline-error-title">
                    <EmptyState
                        illustration={<LogoBubbles />}
                        title={phase.message}
                        titleId="inline-error-title"
                    />
                    <div className="screen-footer">
                        <Button
                            size="lg"
                            block
                            onClick={() => {
                                telegram.close();
                            }}
                        >
                            {ru.composeInlineClose}
                        </Button>
                    </div>
                </section>
            </main>
        );
    }

    if (phase.kind === "limit") {
        return (
            <main className="app-main">
                <LimitScreen
                    kind={phase.limitKind}
                    message={phase.message}
                    onOpenCrisis={() => {
                        setPhase({ kind: "crisis", lead: null, resources: [] });
                    }}
                    onClose={() => {
                        telegram.close();
                    }}
                />
            </main>
        );
    }

    if (phase.kind === "crisis") {
        return (
            <main className="app-main">
                <CrisisScreen
                    lead={phase.lead}
                    resources={phase.resources}
                    onBack={() => {
                        telegram.close();
                    }}
                />
            </main>
        );
    }

    const context = phase.context;
    const draftMax = context.limits.incoming_text_max_chars;

    return (
        <ApiProvider
            credential={{ scheme: "compose", value: context.session.token }}
            {...(fetchImpl !== undefined ? { fetchImpl } : {})}
        >
            <main className="app-main">
                <ComposeEditor
                    telegram={telegram}
                    draftMax={draftMax}
                    initialDraft={sliceCodePoints(context.draft, draftMax)}
                    initialContactId={null}
                    partner={{ mode: "fixed" }}
                    onLimit={(kind, message) => {
                        setPhase({ kind: "limit", limitKind: kind, message });
                    }}
                    onCrisis={(lead, resources) => {
                        setPhase({ kind: "crisis", lead, resources });
                    }}
                    onSessionExpired={() => {
                        setPhase({
                            kind: "error",
                            message: ru.composeInlineSessionExpired,
                        });
                    }}
                />
            </main>
        </ApiProvider>
    );
}
