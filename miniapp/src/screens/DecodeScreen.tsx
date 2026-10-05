import { useEffect, useRef, useState } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapApiResult } from "../api/request";
import { consumeDecodeSse, type DecodeCompletedPayload } from "../api/sse";
import { formatAppliedRuleCitation } from "../decode/formatAppliedRuleCitation";
import { formatDisplayDate } from "../dates/formatDisplayDate";
import { ru, type CategoryKey } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

const DECODE_MAX = 4000;

export type DecodeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly displayTimezone: string;
    readonly activeContactId: string | null;
    readonly onEditSuggestion: (category: string, text: string) => void;
    readonly fetchImpl?: typeof fetch;
};

type Phase =
    | { readonly kind: "idle" }
    | { readonly kind: "streaming"; readonly analysis: string }
    | {
          readonly kind: "completed";
          readonly analysis: string;
          readonly payload: DecodeCompletedPayload;
      }
    | {
          readonly kind: "crisis";
          readonly lead: string;
          readonly resources: readonly string[];
      }
    | { readonly kind: "refused" }
    | { readonly kind: "error"; readonly message: string };

type SuggestionCard = {
    readonly id: string;
    readonly category: string;
    readonly text: string;
};

function errorMessageForCode(code: string): string {
    switch (code) {
        case "busy":
            return ru.decodeErrorBusy;
        case "quota_exceeded":
            return ru.decodeErrorQuota;
        case "text_too_short":
            return ru.decodeErrorShort;
        case "text_too_long":
            return ru.decodeErrorLong;
        case "invalid_output":
            return ru.decodeErrorInvalid;
        default:
            return ru.decodeErrorUnavailable;
    }
}

function firmnessLabel(firmness: string): string {
    if (firmness === "gentle" || firmness === "balanced" || firmness === "firm") {
        return ru.firmness[firmness];
    }
    return firmness;
}

export function DecodeScreen({
    telegram,
    displayTimezone,
    activeContactId,
    onEditSuggestion,
    fetchImpl = fetch,
}: DecodeScreenProps) {
    const client = useApiClient();
    const [text, setText] = useState("");
    const [phase, setPhase] = useState<Phase>({ kind: "idle" });
    const [suggestion, setSuggestion] = useState<SuggestionCard | null>(null);
    const [suggestBusy, setSuggestBusy] = useState(false);
    const [suggestMessage, setSuggestMessage] = useState<string | null>(null);
    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        return () => {
            abortRef.current?.abort();
            abortRef.current = null;
        };
    }, []);

    const submit = async () => {
        const trimmed = text.trim();
        if (trimmed.length === 0 || trimmed.length > DECODE_MAX) {
            setPhase({
                kind: "error",
                message: trimmed.length === 0 ? ru.decodeErrorShort : ru.decodeErrorLong,
            });
            return;
        }
        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;
        setSuggestion(null);
        setSuggestMessage(null);
        setPhase({ kind: "streaming", analysis: "" });

        try {
            const response = await fetchImpl("/api/v1/decode", {
                method: "POST",
                headers: {
                    Authorization: `tma ${telegram.initData}`,
                    "Content-Type": "application/json",
                    Accept: "text/event-stream",
                },
                body: JSON.stringify({ text: trimmed }),
                signal: controller.signal,
            });
            if (!response.ok) {
                let code = "generation_unavailable";
                try {
                    const body = (await response.json()) as { code?: string };
                    if (typeof body.code === "string") {
                        code = body.code;
                    }
                } catch {
                    // keep default
                }
                setPhase({ kind: "error", message: errorMessageForCode(code) });
                telegram.hapticNotification("error");
                return;
            }
            let analysis = "";
            await consumeDecodeSse(
                response,
                {
                    onAnalysis: (chunk) => {
                        analysis += chunk;
                        setPhase({ kind: "streaming", analysis });
                    },
                    onCompleted: (payload) => {
                        setPhase({ kind: "completed", analysis, payload });
                        telegram.hapticNotification("success");
                    },
                    onCrisis: (payload) => {
                        setPhase({
                            kind: "crisis",
                            lead: payload.lead,
                            resources: payload.resources,
                        });
                    },
                    onRefused: () => {
                        setPhase({ kind: "refused" });
                    },
                    onError: (code) => {
                        setPhase({ kind: "error", message: errorMessageForCode(code) });
                        telegram.hapticNotification("error");
                    },
                },
                controller.signal,
            );
        } catch {
            if (controller.signal.aborted) {
                return;
            }
            setPhase({ kind: "error", message: ru.decodeErrorUnavailable });
            telegram.hapticNotification("error");
        }
    };

    const copyVariant = async (variantText: string) => {
        try {
            await navigator.clipboard.writeText(variantText);
            telegram.hapticNotification("success");
        } catch {
            telegram.hapticNotification("error");
        }
    };

    const insertVariant = (insertQuery: string | null) => {
        if (insertQuery === null || insertQuery.length === 0) {
            return;
        }
        telegram.switchInlineQuery(insertQuery, ["users", "groups", "channels"]);
    };

    const makeRule = async (token: string) => {
        setSuggestBusy(true);
        setSuggestMessage(null);
        const unwrapped = unwrapApiResult(
            await client.POST("/api/v1/suggestions/from-decode", {
                body: { token },
            }),
        );
        setSuggestBusy(false);
        if (unwrapped.error !== undefined || unwrapped.data === undefined) {
            setSuggestMessage(ru.decodeErrorUnavailable);
            telegram.hapticNotification("error");
            return;
        }
        const outcome = unwrapped.data.outcome;
        const item = unwrapped.data.suggestion;
        if (outcome === "ok" && item != null) {
            setSuggestion({ id: item.id, category: item.category, text: item.text });
            telegram.hapticNotification("success");
            return;
        }
        if (outcome === "crisis") {
            const lead = unwrapped.data.lead ?? "";
            const resources = unwrapped.data.resources ?? [];
            setPhase({ kind: "crisis", lead, resources });
            return;
        }
        const messages: Record<string, string> = {
            none: ru.decodeSuggestNone,
            unavailable: ru.decodeSuggestUnavailable,
            quota_exceeded: ru.decodeSuggestQuota,
            pending_exists: ru.decodeSuggestPending,
        };
        setSuggestMessage(messages[outcome] ?? ru.decodeErrorUnavailable);
    };

    const acceptSuggestion = async () => {
        if (suggestion === null) {
            return;
        }
        setSuggestBusy(true);
        const unwrapped = unwrapApiResult(
            await client.POST("/api/v1/suggestions/{suggestion_id}/accept", {
                params: { path: { suggestion_id: suggestion.id } },
            }),
        );
        setSuggestBusy(false);
        if (unwrapped.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        setSuggestion(null);
        telegram.hapticNotification("success");
    };

    const dismissSuggestion = async () => {
        if (suggestion === null) {
            return;
        }
        setSuggestBusy(true);
        const unwrapped = unwrapApiResult(
            await client.POST("/api/v1/suggestions/{suggestion_id}/dismiss", {
                params: { path: { suggestion_id: suggestion.id } },
            }),
        );
        setSuggestBusy(false);
        if (unwrapped.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        setSuggestion(null);
    };

    const streaming = phase.kind === "streaming";
    const analysisText =
        phase.kind === "streaming" || phase.kind === "completed" ? phase.analysis : "";

    return (
        <section className="screen" aria-labelledby="decode-title">
            <h2 id="decode-title" className="screen-title">
                {ru.decodeTitle}
            </h2>

            <label className="field">
                <span className="field-label">{ru.decodePlaceholder}</span>
                <textarea
                    className="field-input field-textarea"
                    value={text}
                    maxLength={DECODE_MAX}
                    rows={6}
                    disabled={streaming}
                    onChange={(event) => {
                        setText(event.target.value);
                    }}
                />
                <span className="field-counter" aria-live="polite">
                    {text.length}/{DECODE_MAX}
                </span>
            </label>

            <button
                type="button"
                className="btn btn-primary"
                disabled={streaming || activeContactId === null}
                onClick={() => {
                    void submit();
                }}
            >
                {ru.decodeSubmit}
            </button>

            {analysisText.length > 0 ? (
                <section className="block" aria-labelledby="decode-analysis-title">
                    <h3 id="decode-analysis-title" className="block-title">
                        {ru.decodeAnalysis}
                    </h3>
                    <p className="decode-analysis">{analysisText}</p>
                </section>
            ) : null}

            {phase.kind === "completed" ? (
                <>
                    <ul className="list">
                        {phase.payload.variants.map((variant) => (
                            <li key={`${variant.firmness}-${variant.text}`} className="list-item">
                                <p className="list-item-meta">{firmnessLabel(variant.firmness)}</p>
                                <p className="list-item-title">{variant.text}</p>
                                <div className="list-item-actions">
                                    <button
                                        type="button"
                                        className="btn btn-secondary"
                                        onClick={() => {
                                            void copyVariant(variant.text);
                                        }}
                                    >
                                        {ru.decodeCopy}
                                    </button>
                                    <button
                                        type="button"
                                        className="btn btn-secondary"
                                        disabled={variant.insert_query === null}
                                        onClick={() => {
                                            insertVariant(variant.insert_query);
                                        }}
                                    >
                                        {ru.decodeInsert}
                                    </button>
                                </div>
                            </li>
                        ))}
                    </ul>
                    {phase.payload.applied_rules.length > 0 ? (
                        <ul className="list">
                            {phase.payload.applied_rules.slice(0, 3).map((rule) => (
                                <li
                                    key={`${rule.category}-${rule.text}`}
                                    className="list-item-meta"
                                >
                                    {formatAppliedRuleCitation(
                                        phase.payload.applied_rule_template,
                                        {
                                            date: formatDisplayDate(
                                                rule.effective_since,
                                                displayTimezone,
                                            ),
                                            text: rule.text,
                                        },
                                    )}
                                </li>
                            ))}
                        </ul>
                    ) : null}
                    {phase.payload.rule_source_token !== null ? (
                        <button
                            type="button"
                            className="btn btn-primary"
                            disabled={suggestBusy}
                            onClick={() => {
                                void makeRule(phase.payload.rule_source_token as string);
                            }}
                        >
                            {ru.decodeMakeRule}
                        </button>
                    ) : null}
                </>
            ) : null}

            {phase.kind === "crisis" ? (
                <section className="block">
                    <p>{phase.lead}</p>
                    <ul className="list">
                        {phase.resources.map((line) => (
                            <li key={line} className="list-item-meta">
                                {line}
                            </li>
                        ))}
                    </ul>
                </section>
            ) : null}

            {phase.kind === "refused" ? <p className="field-error">{ru.decodeRefused}</p> : null}
            {phase.kind === "error" ? <p className="field-error">{phase.message}</p> : null}
            {suggestMessage !== null ? <p className="field-error">{suggestMessage}</p> : null}

            {suggestion !== null ? (
                <section className="block" aria-labelledby="decode-suggestion-title">
                    <h3 id="decode-suggestion-title" className="block-title">
                        {ru.suggestionsTitle}
                    </h3>
                    <p className="list-item-title">{suggestion.text}</p>
                    <p className="list-item-meta">
                        {suggestion.category in ru.categories
                            ? ru.categories[suggestion.category as CategoryKey]
                            : suggestion.category}
                    </p>
                    <div className="list-item-actions">
                        <button
                            type="button"
                            className="btn btn-primary"
                            disabled={suggestBusy}
                            onClick={() => {
                                void acceptSuggestion();
                            }}
                        >
                            {ru.suggestionAccept}
                        </button>
                        <button
                            type="button"
                            className="btn btn-secondary"
                            disabled={suggestBusy}
                            onClick={() => {
                                onEditSuggestion(suggestion.category, suggestion.text);
                            }}
                        >
                            {ru.decodeSuggestionEdit}
                        </button>
                        <button
                            type="button"
                            className="btn btn-secondary"
                            disabled={suggestBusy}
                            onClick={() => {
                                void dismissSuggestion();
                            }}
                        >
                            {ru.suggestionDismiss}
                        </button>
                    </div>
                </section>
            ) : null}
        </section>
    );
}
