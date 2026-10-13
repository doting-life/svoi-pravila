import { useEffect, useRef } from "react";

import type { ApiError } from "../api/errors";
import type { ResourceState } from "../hooks/useAsyncResource";
import type { PendingRule } from "../hooks/usePendingRules";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";
import { Button } from "./Button";
import { Card } from "./Card";

export type PendingRulesState = ResourceState<readonly PendingRule[]> & {
    readonly refetch: () => void;
    readonly approveRule: (ruleId: string) => Promise<{ error?: ApiError }>;
    readonly rejectRule: (ruleId: string) => Promise<{ error?: ApiError }>;
};

export type PendingRulesBlockProps = {
    readonly telegram: TelegramAdapter;
    readonly pending: PendingRulesState;
    readonly focusPending: boolean;
};

/** First rule waiting for the user's answer, with approve / reject. */
export function PendingRulesBlock({ telegram, pending, focusPending }: PendingRulesBlockProps) {
    const sectionRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (focusPending && pending.status === "success" && pending.data.length > 0) {
            sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }, [focusPending, pending]);

    if (pending.status !== "success") {
        return null;
    }

    const first = pending.data[0];
    if (first === undefined) {
        return null;
    }
    const more = pending.data.length - 1;

    const decide = async (action: "approve" | "reject") => {
        const result =
            action === "approve"
                ? await pending.approveRule(first.id)
                : await pending.rejectRule(first.id);
        telegram.hapticNotification(result.error === undefined ? "success" : "error");
    };

    return (
        <div ref={sectionRef} data-testid="people-pending">
            <Card tone="warm" aria-labelledby="people-pending-title">
                <h2 id="people-pending-title" className="card__title">
                    {ru.pendingTitle}
                </h2>
                <p className="card__eyebrow-aside">{first.contact_label}</p>
                <p className="rule-text">{first.text}</p>
                <div className="row">
                    <Button
                        className="row__grow"
                        onClick={() => {
                            void decide("approve");
                        }}
                    >
                        {ru.ruleApprove}
                    </Button>
                    <Button
                        className="row__grow"
                        variant="ghost"
                        tone="warm"
                        onClick={() => {
                            void decide("reject");
                        }}
                    >
                        {ru.ruleReject}
                    </Button>
                </div>
                {more > 0 ? (
                    <p className="hint-text">{ru.pendingMore.replace("{n}", String(more))}</p>
                ) : null}
            </Card>
        </div>
    );
}
