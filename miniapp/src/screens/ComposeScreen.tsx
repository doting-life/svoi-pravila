import { useState } from "react";

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
import { useContacts, type Contact } from "../hooks/useContacts";
import { ru } from "../localization/ru";
import type { LimitKind } from "../navigation/stack";
import type { TelegramAdapter } from "../telegram/webapp";

const COMPOSE_MAX = 4000;

type ComposeIntent = components["schemas"]["ComposeRequest"]["intent"];
type Firmness = components["schemas"]["ComposeVariantItem"]["firmness"];

const INTENT_OPTIONS: readonly { readonly value: ComposeIntent; readonly label: string }[] = [
    { value: "soften", label: ru.composeIntents.soften },
    { value: "decline", label: ru.composeIntents.decline },
    { value: "set_boundary", label: ru.composeIntents.set_boundary },
    { value: "admit_fault", label: ru.composeIntents.admit_fault },
    { value: "reconnect", label: ru.composeIntents.reconnect },
    { value: "other", label: ru.composeIntents.other },
];

const FIRMNESS_ORDER: readonly Firmness[] = ["gentle", "balanced", "firm"];

export type ComposeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly activeContactId: string | null;
    readonly onActivated: (contactId: string) => void;
    readonly onLimit: (kind: LimitKind, message: string | null) => void;
    readonly onCrisis: (lead: string | null, resources: readonly string[]) => void;
};

type EditableVariant = {
    readonly firmness: Firmness;
    readonly text: string;
};

type Phase =
    | { readonly kind: "idle" }
    | { readonly kind: "loading" }
    | {
          readonly kind: "result";
          readonly variants: EditableVariant[];
          readonly appliedRules: readonly { readonly index: number; readonly text: string }[];
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
        default:
            return ru.composeErrorUnavailable;
    }
}

function orderedVariants(
    variants: readonly { readonly firmness: Firmness; readonly text: string }[],
): EditableVariant[] {
    const byFirmness = new Map(variants.map((item) => [item.firmness, item.text]));
    return FIRMNESS_ORDER.filter((firmness) => byFirmness.has(firmness)).map((firmness) => ({
        firmness,
        text: byFirmness.get(firmness) ?? "",
    }));
}

export function ComposeScreen({
    telegram,
    activeContactId,
    onActivated,
    onLimit,
    onCrisis,
}: ComposeScreenProps) {
    const client = useApiClient();
    const contacts = useContacts();
    const [intent, setIntent] = useState<ComposeIntent>("soften");
    const [contactId, setContactId] = useState<string | null>(activeContactId);
    const [draft, setDraft] = useState("");
    const [phase, setPhase] = useState<Phase>({ kind: "idle" });
    const [pasteHint, setPasteHint] = useState<string | null>(null);
    const [copyHint, setCopyHint] = useState<string | null>(null);
    const [pickerOpen, setPickerOpen] = useState(false);
    const [busyChoice, setBusyChoice] = useState(false);

    const contactList = contacts.status === "success" ? contacts.data : [];
    const selected = contactList.find((contact) => contact.id === contactId);

    const pasteFromClipboard = async () => {
        setPasteHint(null);
        try {
            const clipboardText = await navigator.clipboard.readText();
            setDraft(clipboardText.slice(0, COMPOSE_MAX));
        } catch {
            setPasteHint(ru.composePasteFailed);
        }
    };

    const selectContact = async (contact: Contact) => {
        setPickerOpen(false);
        setContactId(contact.id);
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

    const selectNone = () => {
        setPickerOpen(false);
        setContactId(null);
    };

    const submit = async () => {
        const trimmed = draft.trim();
        if (trimmed.length === 0 || trimmed.length > COMPOSE_MAX) {
            setPhase({
                kind: "error",
                message: trimmed.length === 0 ? ru.composeErrorShort : ru.composeErrorLong,
            });
            return;
        }
        setCopyHint(null);
        setPhase({ kind: "loading" });
        const unwrapped = unwrapApiResult(
            await client.POST("/api/v1/compose", {
                body: {
                    draft: trimmed,
                    intent,
                    contact_id: contactId,
                },
            }),
        );
        if (unwrapped.error !== undefined || unwrapped.data === undefined) {
            telegram.hapticNotification("error");
            const limitKind = limitKindForCode(unwrapped.error?.code);
            if (limitKind !== null) {
                onLimit(limitKind, unwrapped.error?.message || null);
                setPhase({ kind: "idle" });
                return;
            }
            setPhase({
                kind: "error",
                message: errorMessageForCode(unwrapped.error?.code ?? "generation_unavailable"),
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
        telegram.hapticNotification("success");
        setPhase({
            kind: "result",
            variants: orderedVariants(data.variants),
            appliedRules: data.applied_rules,
        });
    };

    const updateVariantText = (firmness: Firmness, text: string) => {
        setPhase((current) => {
            if (current.kind !== "result") {
                return current;
            }
            return {
                ...current,
                variants: current.variants.map((variant) =>
                    variant.firmness === firmness ? { ...variant, text } : variant,
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

    const copyAndReturn = async (text: string, firmness: Firmness) => {
        if (busyChoice) {
            return;
        }
        const ok = await copyText(text);
        if (!ok) {
            return;
        }
        setCopyHint(ru.composePasteHint);
        setBusyChoice(true);
        const scenario = intent === "soften" ? "soften" : "help_say";
        const unwrapped = unwrapApiResult(
            await client.POST("/api/v1/compose/choice", {
                body: { scenario, firmness },
            }),
        );
        setBusyChoice(false);
        if (unwrapped.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.close();
    };

    const reset = () => {
        setDraft("");
        setCopyHint(null);
        setPhase({ kind: "idle" });
    };

    if (phase.kind === "loading") {
        return (
            <section className="screen" aria-labelledby="compose-title">
                <ScreenHeader title={ru.composeTitle} titleId="compose-title" />
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
                <ScreenHeader title={ru.composeTitle} titleId="compose-title" />

                <section className="section" aria-labelledby="compose-results-title">
                    <h2 id="compose-results-title" className="section-title">
                        {ru.composeResults}
                    </h2>
                    <ul className="rule-list">
                        {phase.variants.map((variant) => (
                            <li key={variant.firmness}>
                                <Card as="article" className="variant">
                                    <TextArea
                                        label={ru.composeFirmness[variant.firmness]}
                                        value={variant.text}
                                        maxLength={COMPOSE_MAX}
                                        rows={4}
                                        large
                                        onChange={(next) => {
                                            updateVariantText(variant.firmness, next);
                                        }}
                                    />
                                    <div className="variant__actions">
                                        <Button
                                            disabled={
                                                busyChoice || variant.text.trim().length === 0
                                            }
                                            onClick={() => {
                                                void copyAndReturn(variant.text, variant.firmness);
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
            <ScreenHeader title={ru.composeTitle} titleId="compose-title" lead={ru.composeLead} />

            <ChipGroup
                legend={ru.composeIntentLegend}
                options={INTENT_OPTIONS}
                value={intent}
                onChange={setIntent}
            />

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

            <TextArea
                label={ru.composeField}
                value={draft}
                maxLength={COMPOSE_MAX}
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
                onChange={setDraft}
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
                    {selected !== undefined
                        ? ru.composePrivacyNamed.replace("{name}", selected.label)
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
            </div>

            {pickerOpen ? (
                <ContactPicker
                    contacts={contactList}
                    activeContactId={contactId}
                    noneOption={{
                        label: ru.pickerNone,
                        selected: contactId === null,
                        onSelect: selectNone,
                    }}
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
