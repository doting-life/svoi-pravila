import { useEffect, useRef, useState } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapApiResult } from "../api/request";
import type { components } from "../api/schema";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ChipGroup } from "../components/ChipGroup";
import { ContactPicker } from "../components/ContactPicker";
import { TextArea } from "../components/Field";
import { Avatar } from "../components/Avatar";
import { ChevronDownIcon, ClipboardIcon } from "../components/icons";
import { ScreenHeader } from "../components/ScreenHeader";
import { Skeleton } from "../components/Skeleton";
import { partnerSendUrl } from "../compose/partnerSendUrl";
import { useAsyncResource } from "../hooks/useAsyncResource";
import { useContacts, type Contact } from "../hooks/useContacts";
import { ru } from "../localization/ru";
import type { ComposeMode, LimitKind } from "../navigation/stack";
import { codePointLength } from "../rules/ruleText";
import type { TelegramAdapter } from "../telegram/webapp";

function sliceCodePoints(text: string, max: number): string {
    return Array.from(text).slice(0, max).join("");
}

type ComposeGoal = NonNullable<components["schemas"]["ComposeRequest"]["goal"]>;
type VariantKind = components["schemas"]["ComposeVariantItem"]["kind"];

const GOAL_OPTIONS: readonly { readonly value: ComposeGoal; readonly label: string }[] = [
    { value: "decline", label: ru.composeGoals.decline },
    { value: "set_boundary", label: ru.composeGoals.set_boundary },
    { value: "admit_fault", label: ru.composeGoals.admit_fault },
    { value: "reconnect", label: ru.composeGoals.reconnect },
];

const KIND_ORDER: Readonly<Record<ComposeMode, readonly VariantKind[]>> = {
    check: ["check", "softer", "firmer"],
    soften: ["softer", "firmer", "check"],
};

export type ComposePartnerProps =
    | { readonly mode: "inline" }
    | { readonly mode: "selectable"; readonly contacts: ReturnType<typeof useContacts> };

export type ComposeEditorProps = {
    readonly telegram: TelegramAdapter;
    readonly mode: ComposeMode;
    readonly title?: string;
    /** Request variants once on mount when a draft is already provided. */
    readonly autoRun?: boolean;
    readonly draftMax: number;
    readonly initialDraft: string;
    readonly initialContactId: string | null;
    readonly partner: ComposePartnerProps;
    readonly onLimit: (kind: LimitKind, message: string | null) => void;
    readonly onCrisis: (lead: string | null, resources: readonly string[]) => void;
    readonly onSessionExpired?: () => void;
};

type EditableVariant = {
    readonly kind: VariantKind;
    readonly text: string;
};

type Phase =
    | { readonly kind: "idle" }
    | { readonly kind: "loading" }
    | {
          readonly kind: "result";
          readonly variants: EditableVariant[];
          readonly appliedRules: readonly { readonly index: number; readonly text: string }[];
          readonly choiceToken: string;
          readonly goal: ComposeGoal | null;
      }
    | { readonly kind: "refused" }
    | { readonly kind: "error"; readonly message: string };

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
            return ru.composeErrorBusy;
        case "quota_exceeded":
            return ru.composeErrorQuota;
        case "text_too_short":
            return ru.composeErrorShort;
        case "text_too_long":
            return ru.composeErrorLong;
        case "invalid_output":
            return ru.composeErrorInvalid;
        case "compose_session_expired":
            return ru.composeInlineSessionExpired;
        default:
            return ru.composeErrorUnavailable;
    }
}

function orderedVariants(
    variants: readonly { readonly kind: VariantKind; readonly text: string }[],
    mode: ComposeMode,
): EditableVariant[] {
    const byKind = new Map(variants.map((item) => [item.kind, item.text]));
    return KIND_ORDER[mode]
        .filter((kind) => byKind.has(kind))
        .map((kind) => ({
            kind,
            text: byKind.get(kind) ?? "",
        }));
}

/** Presentational compose editor shared by full-app and inline launches. */
export function ComposeEditor({
    telegram,
    mode,
    title,
    autoRun = false,
    draftMax,
    initialDraft,
    initialContactId,
    partner,
    onLimit,
    onCrisis,
    onSessionExpired,
}: ComposeEditorProps) {
    const client = useApiClient();
    const allowContactPicker = partner.mode === "selectable";
    const contacts = partner.mode === "selectable" ? partner.contacts : null;
    const showClose = partner.mode === "inline";
    const screenTitle = title ?? (mode === "soften" ? ru.composeTitle : ru.composeTitleCheck);
    const showGoals = mode === "soften";
    const [goal, setGoal] = useState<ComposeGoal | null>(null);
    const [contactId, setContactId] = useState<string | null>(initialContactId);
    const [draft, setDraft] = useState(initialDraft);
    const [phase, setPhase] = useState<Phase>(
        autoRun && initialDraft.trim().length > 0 ? { kind: "loading" } : { kind: "idle" },
    );
    const [pasteHint, setPasteHint] = useState<string | null>(null);
    const [copyHint, setCopyHint] = useState<string | null>(null);
    const [pickerOpen, setPickerOpen] = useState(false);
    const [busyChoice, setBusyChoice] = useState(false);
    const contactList = contacts !== null && contacts.status === "success" ? contacts.data : [];
    const selected = contactList.find((contact) => contact.id === contactId);
    const displayLabel = selected !== undefined ? selected.label : null;
    const pairedContactId = selected != null && selected.paired ? selected.id : null;
    const partnerUsernameResource = useAsyncResource(
        async () => {
            if (pairedContactId == null) {
                return { data: { username: null as string | null } };
            }
            return unwrapApiResult(
                await client.GET("/api/v1/contacts/{contact_id}/partner-username", {
                    params: { path: { contact_id: pairedContactId } },
                }),
            );
        },
        pairedContactId == null ? "partner:none" : `partner:${pairedContactId}`,
    );
    const partnerUsername =
        pairedContactId != null && partnerUsernameResource.status === "success"
            ? (partnerUsernameResource.data.username ?? null)
            : null;

    const pasteFromClipboard = async () => {
        setPasteHint(null);
        try {
            const clipboardText = await navigator.clipboard.readText();
            setDraft(sliceCodePoints(clipboardText, draftMax));
        } catch {
            setPasteHint(ru.composePasteFailed);
        }
    };

    const selectContact = (contact: Contact) => {
        setPickerOpen(false);
        setContactId(contact.id);
    };

    const selectNone = () => {
        setPickerOpen(false);
        setContactId(null);
    };

    const submit = async () => {
        const trimmed = draft.trim();
        const trimmedPoints = codePointLength(trimmed);
        if (trimmedPoints === 0 || trimmedPoints > draftMax) {
            setPhase({
                kind: "error",
                message: trimmedPoints === 0 ? ru.composeErrorShort : ru.composeErrorLong,
            });
            return;
        }
        setCopyHint(null);
        setPhase({ kind: "loading" });
        const unwrapped = unwrapApiResult(
            await client.POST("/api/v1/compose", {
                body: {
                    draft: trimmed,
                    goal,
                    contact_id: contactId,
                },
            }),
        );
        if (unwrapped.error !== undefined || unwrapped.data === undefined) {
            telegram.hapticNotification("error");
            const code = unwrapped.error?.code;
            const limitKind = limitKindForCode(code);
            if (limitKind !== null) {
                onLimit(limitKind, unwrapped.error?.message || null);
                setPhase({ kind: "idle" });
                return;
            }
            if (code === "compose_session_expired" && onSessionExpired !== undefined) {
                onSessionExpired();
                setPhase({ kind: "idle" });
                return;
            }
            setPhase({
                kind: "error",
                message: errorMessageForCode(code ?? "generation_unavailable"),
            });
            return;
        }
        const data = unwrapped.data;
        if (data.safety === "crisis") {
            onCrisis(data.lead ?? null, data.resources ?? []);
            setPhase({ kind: "idle" });
            return;
        }
        if (data.safety === "refuse_manipulation") {
            setPhase({ kind: "refused" });
            return;
        }
        if (data.choice_token == null || data.choice_token.length === 0) {
            setPhase({ kind: "error", message: ru.composeErrorUnavailable });
            return;
        }
        telegram.hapticNotification("success");
        setPhase({
            kind: "result",
            variants: orderedVariants(data.variants, mode),
            appliedRules: data.applied_rules,
            choiceToken: data.choice_token,
            goal,
        });
    };

    const submitRef = useRef(submit);
    const autoRunStarted = useRef(false);

    useEffect(() => {
        submitRef.current = submit;
    });

    useEffect(() => {
        if (autoRun && !autoRunStarted.current && initialDraft.trim().length > 0) {
            autoRunStarted.current = true;
            void submitRef.current();
        }
    }, [autoRun, initialDraft]);

    const updateVariantText = (kind: VariantKind, text: string) => {
        setPhase((current) => {
            if (current.kind !== "result") {
                return current;
            }
            return {
                ...current,
                variants: current.variants.map((variant) =>
                    variant.kind === kind ? { ...variant, text } : variant,
                ),
            };
        });
    };

    const copyText = async (text: string): Promise<boolean> => {
        const ok = await telegram.copyText(text);
        telegram.hapticNotification(ok ? "success" : "error");
        return ok;
    };

    const copyOnly = async (text: string) => {
        const ok = await copyText(text);
        if (ok) {
            setCopyHint(ru.composePasteHint);
        }
    };

    const recordChoiceAndClose = async (kind: VariantKind, choiceToken: string) => {
        setBusyChoice(true);
        const unwrapped = unwrapApiResult(
            await client.POST("/api/v1/compose/choice", {
                body: { token: choiceToken, kind },
            }),
        );
        setBusyChoice(false);
        if (unwrapped.error !== undefined) {
            telegram.hapticNotification("error");
        }
        telegram.close();
    };

    const copyAndReturn = async (text: string, kind: VariantKind, choiceToken: string) => {
        if (busyChoice) {
            return;
        }
        const ok = await copyText(text);
        if (!ok) {
            return;
        }
        setCopyHint(ru.composePasteHint);
        await recordChoiceAndClose(kind, choiceToken);
    };

    const sendAndReturn = async (text: string, kind: VariantKind, choiceToken: string) => {
        if (busyChoice || partnerUsername == null || displayLabel == null) {
            return;
        }
        telegram.openTelegramLink(partnerSendUrl(partnerUsername, text));
        telegram.hapticNotification("success");
        await recordChoiceAndClose(kind, choiceToken);
    };

    const goalChips = (
        <ChipGroup
            legend={ru.composeGoalLegend}
            options={GOAL_OPTIONS}
            value={goal}
            onChange={setGoal}
            onClear={() => {
                setGoal(null);
            }}
        />
    );

    const reset = () => {
        setDraft("");
        setCopyHint(null);
        setPhase({ kind: "idle" });
    };

    if (phase.kind === "loading") {
        return (
            <section className="screen" aria-labelledby="compose-title">
                <ScreenHeader title={screenTitle} titleId="compose-title" />
                <div role="status">
                    <span className="visually-hidden">{ru.loading}</span>
                    <Skeleton shape="block" />
                    <Skeleton shape="line" />
                    <Skeleton shape="line" short />
                </div>
            </section>
        );
    }

    if (phase.kind === "result") {
        return (
            <section className="screen" aria-labelledby="compose-title">
                <ScreenHeader title={screenTitle} titleId="compose-title" />

                {showGoals ? goalChips : null}
                {showGoals && goal !== phase.goal ? (
                    <Button
                        variant="outline"
                        onClick={() => {
                            void submit();
                        }}
                    >
                        {ru.composeRegenerate}
                    </Button>
                ) : null}

                <section className="section" aria-labelledby="compose-results-title">
                    <h2 id="compose-results-title" className="section-title">
                        {ru.composeResults}
                    </h2>
                    <ul className="rule-list">
                        {phase.variants.map((variant) => (
                            <li key={variant.kind}>
                                <Card as="article" className="variant">
                                    <TextArea
                                        label={ru.variantKinds[variant.kind]}
                                        value={variant.text}
                                        maxLength={1000}
                                        rows={4}
                                        large
                                        onChange={(next) => {
                                            updateVariantText(variant.kind, next);
                                        }}
                                    />
                                    <div className="variant__actions">
                                        {partnerUsername != null && displayLabel != null ? (
                                            <Button
                                                disabled={
                                                    busyChoice || variant.text.trim().length === 0
                                                }
                                                onClick={() => {
                                                    void sendAndReturn(
                                                        variant.text,
                                                        variant.kind,
                                                        phase.choiceToken,
                                                    );
                                                }}
                                            >
                                                {ru.composeSend.replace("{label}", displayLabel)}
                                            </Button>
                                        ) : null}
                                        <Button
                                            variant={
                                                partnerUsername != null ? "outline" : "primary"
                                            }
                                            disabled={
                                                busyChoice || variant.text.trim().length === 0
                                            }
                                            onClick={() => {
                                                void copyAndReturn(
                                                    variant.text,
                                                    variant.kind,
                                                    phase.choiceToken,
                                                );
                                            }}
                                        >
                                            {ru.composeCopyAndReturn}
                                        </Button>
                                        <Button
                                            variant="outline"
                                            disabled={variant.text.trim().length === 0}
                                            onClick={() => {
                                                void copyOnly(variant.text);
                                            }}
                                        >
                                            {ru.composeCopy}
                                        </Button>
                                    </div>
                                </Card>
                            </li>
                        ))}
                    </ul>
                </section>

                {phase.appliedRules.length > 0 ? (
                    <ul className="stack stack--tight">
                        {phase.appliedRules.map((rule) => (
                            <li key={`${String(rule.index)}-${rule.text}`} className="citation">
                                {ru.composeAppliedRule.replace("{text}", rule.text)}
                            </li>
                        ))}
                    </ul>
                ) : null}

                {copyHint !== null ? (
                    <p className="hint-text" role="status">
                        {copyHint}
                    </p>
                ) : null}

                <div className="screen-footer">
                    <Button variant="outline" size="lg" block onClick={reset}>
                        {ru.composeAgain}
                    </Button>
                </div>
            </section>
        );
    }

    return (
        <section className="screen" aria-labelledby="compose-title">
            <ScreenHeader title={screenTitle} titleId="compose-title" lead={ru.composeLead} />

            {showGoals ? goalChips : null}

            {allowContactPicker ? (
                <button
                    type="button"
                    className="contact-chip"
                    onClick={() => {
                        setPickerOpen(true);
                    }}
                >
                    {selected !== undefined ? <Avatar name={selected.label} size="sm" /> : null}
                    <span>
                        {selected !== undefined
                            ? ru.composeFor.replace("{label}", selected.label)
                            : ru.pickerNone}
                    </span>
                    <ChevronDownIcon size={18} />
                </button>
            ) : null}

            <TextArea
                label={ru.composeField}
                value={draft}
                maxLength={draftMax}
                countCodePoints
                placeholder={ru.composePlaceholder}
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
                        {ru.composePaste}
                    </button>
                }
                onChange={(next) => {
                    setDraft(sliceCodePoints(next, draftMax));
                }}
            />

            {pasteHint !== null ? (
                <p className="form-error" role="alert">
                    {pasteHint}
                </p>
            ) : null}
            {phase.kind === "refused" ? (
                <p className="form-error" role="alert">
                    {ru.composeRefused}
                </p>
            ) : null}
            {phase.kind === "error" ? (
                <p className="form-error" role="alert">
                    {phase.message}
                </p>
            ) : null}

            <div className="screen-footer">
                <p className="hint-text">
                    {displayLabel != null
                        ? ru.composePrivacyNamed.replace("{name}", displayLabel)
                        : ru.composePrivacy}
                </p>
                <Button
                    size="lg"
                    block
                    onClick={() => {
                        void submit();
                    }}
                >
                    {ru.composeSubmit}
                </Button>
                {showClose ? (
                    <Button
                        variant="outline"
                        size="lg"
                        block
                        onClick={() => {
                            telegram.close();
                        }}
                    >
                        {ru.composeInlineClose}
                    </Button>
                ) : null}
            </div>

            {pickerOpen && allowContactPicker ? (
                <ContactPicker
                    contacts={contactList}
                    selectedContactId={contactId}
                    noneOption={{
                        label: ru.pickerNone,
                        selected: contactId === null,
                        onSelect: selectNone,
                    }}
                    onSelect={selectContact}
                    onClose={() => {
                        setPickerOpen(false);
                    }}
                />
            ) : null}
        </section>
    );
}

export type ComposeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly mode: ComposeMode;
    readonly title?: string;
    readonly draft: string;
    readonly contactId: string | null;
    readonly draftMax: number;
    readonly onLimit: (kind: LimitKind, message: string | null) => void;
    readonly onCrisis: (lead: string | null, resources: readonly string[]) => void;
};

/** Full-app variants screen (FULL scope via ``tma`` credential); requests variants on open. */
export function ComposeScreen(props: ComposeScreenProps) {
    const contacts = useContacts();
    return (
        <ComposeEditor
            telegram={props.telegram}
            mode={props.mode}
            {...(props.title !== undefined ? { title: props.title } : {})}
            autoRun
            draftMax={props.draftMax}
            initialDraft={props.draft}
            initialContactId={props.contactId}
            partner={{ mode: "selectable", contacts }}
            onLimit={props.onLimit}
            onCrisis={props.onCrisis}
        />
    );
}
