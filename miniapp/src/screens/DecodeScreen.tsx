import { useEffect, useRef, useState } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapApiResult } from "../api/request";
import { consumeDecodeSse, type DecodeCompletedPayload } from "../api/sse";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ContactPicker } from "../components/ContactPicker";
import { TextArea } from "../components/Field";
import { Avatar } from "../components/Avatar";
import { ChevronDownIcon, ClipboardIcon } from "../components/icons";
import { ScreenHeader } from "../components/ScreenHeader";
import { Skeleton } from "../components/Skeleton";
import { formatDisplayDate } from "../dates/formatDisplayDate";
import { formatAppliedRuleCitation } from "../decode/formatAppliedRuleCitation";
import { useContacts, type Contact } from "../hooks/useContacts";
import { decodeRemainingLabel } from "../localization/plural";
import { ru, type CategoryKey } from "../localization/ru";
import type { LimitKind } from "../navigation/stack";
import type { TelegramAdapter } from "../telegram/webapp";

const DECODE_MAX = 4000;

export type DecodeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly displayTimezone: string;
    readonly activeContactId: string | null;
    readonly decodeRemaining: number | null;
    readonly onDecodeUsed: () => void;
    readonly onActivated: (contactId: string) => void;
    readonly onEditSuggestion: (contact: Contact, category: string, text: string) => void;
    readonly onLimit: (kind: LimitKind, message: string | null) => void;
    readonly onCrisis: (lead: string | null, resources: readonly string[]) => void;
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
    | { readonly kind: "refused" }
    | { readonly kind: "error"; readonly message: string };

type SuggestionCard = {
    readonly id: string;
    readonly category: string;
    readonly text: string;
};

function limitKindForCode(code: string | undefined): LimitKind | null {
    if (code === "quota_exhausted") {
        return "quota";
    }
    if (code === "service_budget_exhausted") {
        return "budget";
    }
    return null;
}

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
    decodeRemaining,
    onDecodeUsed,
    onActivated,
    onEditSuggestion,
    onLimit,
    onCrisis,
    fetchImpl = fetch,
}: DecodeScreenProps) {
    const client = useApiClient();
    const contacts = useContacts();
    const [text, setText] = useState("");
    const [phase, setPhase] = useState<Phase>({ kind: "idle" });
    const [suggestion, setSuggestion] = useState<SuggestionCard | null>(null);
    const [suggestBusy, setSuggestBusy] = useState(false);
    const [suggestMessage, setSuggestMessage] = useState<string | null>(null);
    const [pasteHint, setPasteHint] = useState<string | null>(null);
    const [pickerOpen, setPickerOpen] = useState(false);
    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        return () => {
            abortRef.current?.abort();
            abortRef.current = null;
        };
    }, []);

    const contactList = contacts.status === "success" ? contacts.data : [];
    const active = contactList.find((contact) => contact.id === activeContactId);

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
                let serverMessage: string | null = null;
                try {
                    const body = (await response.json()) as { code?: string; message?: string };
                    if (typeof body.code === "string") {
                        code = body.code;
                    }
                    if (typeof body.message === "string" && body.message.length > 0) {
                        serverMessage = body.message;
                    }
                } catch {
                    // keep default
                }
                telegram.hapticNotification("error");
                const limitKind = limitKindForCode(code);
                if (limitKind !== null) {
                    onLimit(limitKind, serverMessage);
                    return;
                }
                setPhase({ kind: "error", message: errorMessageForCode(code) });
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
                        onDecodeUsed();
                    },
                    onCrisis: (payload) => {
                        onCrisis(payload.lead, payload.resources);
                    },
                    onRefused: () => {
                        setPhase({ kind: "refused" });
                    },
                    onError: (code) => {
                        telegram.hapticNotification("error");
                        const limitKind = limitKindForCode(code);
                        if (limitKind !== null) {
                            onLimit(limitKind, null);
                            return;
                        }
                        setPhase({ kind: "error", message: errorMessageForCode(code) });
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

    const pasteFromClipboard = async () => {
        setPasteHint(null);
        try {
            const clipboardText = await navigator.clipboard.readText();
            setText(clipboardText.slice(0, DECODE_MAX));
        } catch {
            setPasteHint(ru.decodePasteFailed);
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
            telegram.hapticNotification("error");
            const limitKind = limitKindForCode(unwrapped.error?.code);
            if (limitKind !== null) {
                onLimit(limitKind, unwrapped.error?.message || null);
                return;
            }
            setSuggestMessage(ru.decodeErrorUnavailable);
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
            onCrisis(unwrapped.data.lead ?? null, unwrapped.data.resources ?? []);
            return;
        }
        const messages: Record<string, string> = {
            none: ru.decodeSuggestNone,
            unavailable: ru.decodeSuggestUnavailable,
            pending_exists: ru.decodeSuggestPending,
        };
        setSuggestMessage(messages[outcome] ?? ru.decodeErrorUnavailable);
    };

    const settleSuggestion = async (action: "accept" | "dismiss") => {
        if (suggestion === null) {
            return;
        }
        setSuggestBusy(true);
        const failed =
            action === "accept"
                ? unwrapApiResult(
                      await client.POST("/api/v1/suggestions/{suggestion_id}/accept", {
                          params: { path: { suggestion_id: suggestion.id } },
                      }),
                  ).error !== undefined
                : unwrapApiResult(
                      await client.POST("/api/v1/suggestions/{suggestion_id}/dismiss", {
                          params: { path: { suggestion_id: suggestion.id } },
                      }),
                  ).error !== undefined;
        setSuggestBusy(false);
        if (failed) {
            telegram.hapticNotification("error");
            return;
        }
        setSuggestion(null);
        if (action === "accept") {
            telegram.hapticNotification("success");
        }
    };

    const selectContact = async (contact: Contact) => {
        setPickerOpen(false);
        if (contact.id === activeContactId) {
            return;
        }
        const result = await contacts.activateContact(contact.id);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        onActivated(contact.id);
    };

    const reset = () => {
        abortRef.current?.abort();
        abortRef.current = null;
        setText("");
        setSuggestion(null);
        setSuggestMessage(null);
        setPhase({ kind: "idle" });
    };

    const showingResult = phase.kind === "streaming" || phase.kind === "completed";
    const analysisText = showingResult ? phase.analysis : "";

    if (showingResult) {
        const ruleToken = phase.kind === "completed" ? phase.payload.rule_source_token : null;
        return (
            <section className="screen" aria-labelledby="decode-title">
                <ScreenHeader title={ru.decodeTitle} titleId="decode-title" />

                <Card className="quote" tone="sunken">
                    <span className="quote__label">{ru.decodeIncoming}</span>
                    <p className="quote__text">{text.trim()}</p>
                </Card>

                <section className="section" aria-labelledby="decode-analysis-title">
                    <h2 id="decode-analysis-title" className="section-title">
                        {ru.decodeAnalysis}
                    </h2>
                    {analysisText.length > 0 ? (
                        <p className="analysis-text">{analysisText}</p>
                    ) : (
                        <div role="status">
                            <span className="visually-hidden">{ru.loading}</span>
                            <Skeleton shape="line" />
                            <Skeleton shape="line" short />
                        </div>
                    )}
                </section>

                {phase.kind === "completed" ? (
                    <>
                        <section className="section" aria-labelledby="decode-replies-title">
                            <h2 id="decode-replies-title" className="section-title">
                                {ru.decodeReplies}
                            </h2>
                            <ul className="rule-list">
                                {phase.payload.variants.map((variant) => (
                                    <li key={`${variant.firmness}-${variant.text}`}>
                                        <Card as="article" className="variant">
                                            <Badge tone="neutral">
                                                {firmnessLabel(variant.firmness)}
                                            </Badge>
                                            <p className="variant__text">{variant.text}</p>
                                            <div className="variant__actions">
                                                <Button
                                                    disabled={variant.insert_query === null}
                                                    onClick={() => {
                                                        insertVariant(variant.insert_query);
                                                    }}
                                                >
                                                    {ru.decodeInsert}
                                                </Button>
                                                <Button
                                                    variant="outline"
                                                    onClick={() => {
                                                        void copyVariant(variant.text);
                                                    }}
                                                >
                                                    {ru.decodeCopy}
                                                </Button>
                                            </div>
                                        </Card>
                                    </li>
                                ))}
                            </ul>
                        </section>

                        {phase.payload.applied_rules.length > 0 ? (
                            <ul className="stack stack--tight">
                                {phase.payload.applied_rules.slice(0, 3).map((rule) => (
                                    <li key={`${rule.category}-${rule.text}`} className="citation">
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

                        {ruleToken !== null && suggestion === null ? (
                            <Card tone="warm" aria-labelledby="decode-rule-title">
                                <h2 id="decode-rule-title" className="hint-title">
                                    {ru.decodeRuleTitle}
                                </h2>
                                <p className="hint-text">{ru.decodeRuleText}</p>
                                <Button
                                    disabled={suggestBusy}
                                    onClick={() => {
                                        void makeRule(ruleToken);
                                    }}
                                >
                                    {ru.decodeMakeRule}
                                </Button>
                            </Card>
                        ) : null}

                        {suggestMessage !== null ? (
                            <p className="form-error" role="alert">
                                {suggestMessage}
                            </p>
                        ) : null}

                        {suggestion !== null ? (
                            <Card tone="warm" aria-labelledby="decode-suggestion-title">
                                <h2 id="decode-suggestion-title" className="hint-title">
                                    {ru.suggestionsTitle}
                                </h2>
                                <p className="rule-text">{suggestion.text}</p>
                                <p className="rule-card__meta">
                                    {suggestion.category in ru.categories
                                        ? ru.categories[suggestion.category as CategoryKey]
                                        : suggestion.category}
                                </p>
                                <div className="rule-card__footer">
                                    <Button
                                        disabled={suggestBusy}
                                        onClick={() => {
                                            void settleSuggestion("accept");
                                        }}
                                    >
                                        {ru.suggestionAccept}
                                    </Button>
                                    <Button
                                        variant="outline"
                                        disabled={suggestBusy}
                                        onClick={() => {
                                            if (active !== undefined) {
                                                onEditSuggestion(
                                                    active,
                                                    suggestion.category,
                                                    suggestion.text,
                                                );
                                            }
                                        }}
                                    >
                                        {ru.decodeSuggestionEdit}
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        tone="warm"
                                        disabled={suggestBusy}
                                        onClick={() => {
                                            void settleSuggestion("dismiss");
                                        }}
                                    >
                                        {ru.suggestionDismiss}
                                    </Button>
                                </div>
                            </Card>
                        ) : null}

                        <div className="screen-footer">
                            <Button variant="outline" size="lg" block onClick={reset}>
                                {ru.decodeAgain}
                            </Button>
                        </div>
                    </>
                ) : null}
            </section>
        );
    }

    return (
        <section className="screen" aria-labelledby="decode-title">
            <ScreenHeader
                title={ru.decodeTitle}
                titleId="decode-title"
                lead={ru.decodeLead}
                trailing={
                    decodeRemaining !== null ? (
                        <Badge tone="accent">{decodeRemainingLabel(decodeRemaining)}</Badge>
                    ) : undefined
                }
            />

            <button
                type="button"
                className="contact-chip"
                onClick={() => {
                    setPickerOpen(true);
                }}
            >
                {active !== undefined ? <Avatar name={active.label} size="sm" /> : null}
                <span>
                    {active !== undefined
                        ? ru.decodeFrom.replace("{name}", active.label)
                        : ru.decodeFromNone}
                </span>
                <ChevronDownIcon size={18} />
            </button>

            <TextArea
                label={ru.decodeField}
                value={text}
                maxLength={DECODE_MAX}
                placeholder={ru.decodePlaceholder}
                rows={7}
                large
                footer={
                    <button
                        type="button"
                        className="paste-button"
                        onClick={() => {
                            void pasteFromClipboard();
                        }}
                    >
                        <ClipboardIcon size={18} />
                        {ru.decodePaste}
                    </button>
                }
                onChange={setText}
            />

            {pasteHint !== null ? (
                <p className="form-error" role="alert">
                    {pasteHint}
                </p>
            ) : null}
            {phase.kind === "refused" ? (
                <p className="form-error" role="alert">
                    {ru.decodeRefused}
                </p>
            ) : null}
            {phase.kind === "error" ? (
                <p className="form-error" role="alert">
                    {phase.message}
                </p>
            ) : null}

            <div className="screen-footer">
                <p className="hint-text">
                    {active !== undefined
                        ? ru.decodePrivacyNamed.replace("{name}", active.label)
                        : ru.decodePrivacy}
                </p>
                <Button
                    size="lg"
                    block
                    disabled={activeContactId === null}
                    onClick={() => {
                        void submit();
                    }}
                >
                    {ru.decodeSubmit}
                </Button>
            </div>

            {pickerOpen ? (
                <ContactPicker
                    contacts={contactList}
                    activeContactId={activeContactId}
                    onSelect={(contact) => {
                        void selectContact(contact);
                    }}
                    onClose={() => {
                        setPickerOpen(false);
                    }}
                />
            ) : null}
        </section>
    );
}
