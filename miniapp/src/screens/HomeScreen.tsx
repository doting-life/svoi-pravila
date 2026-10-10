import { useEffect, useRef, useState } from "react";

import { Avatar } from "../components/Avatar";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ContactPicker } from "../components/ContactPicker";
import { ChatIcon, KeyboardIcon } from "../components/icons";
import { Skeleton } from "../components/Skeleton";
import { ErrorView } from "../components/StatusViews";
import type { ApiError } from "../api/errors";
import { greetingFor } from "../home/greeting";
import type { ResourceState } from "../hooks/useAsyncResource";
import { useContacts, type Contact } from "../hooks/useContacts";
import type { PendingRule } from "../hooks/usePendingRules";
import { useRules } from "../hooks/useRules";
import { useSuggestions } from "../hooks/useSuggestions";
import { homeRulesCountLabel } from "../localization/plural";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type PendingRulesState = ResourceState<readonly PendingRule[]> & {
    readonly refetch: () => void;
    readonly approveRule: (ruleId: string) => Promise<{ error?: ApiError }>;
    readonly rejectRule: (ruleId: string) => Promise<{ error?: ApiError }>;
};

export type HomeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly activeContactId: string | null | undefined;
    readonly botUsername: string;
    readonly pendingRules: PendingRulesState;
    readonly focusPending?: boolean;
    readonly onOpenDecode: () => void;
    readonly onOpenPeople: () => void;
    readonly onOpenContactRules: (contact: Contact) => void;
    readonly onAddRule: (contact: Contact) => void;
    readonly onActivated: (contactId: string) => void;
    readonly now?: Date;
};

function oneLine(text: string): string {
    const flat = text.replace(/\s+/gu, " ").trim();
    if (flat.length <= 80) {
        return flat;
    }
    return `${flat.slice(0, 79)}…`;
}

function SuggestionCard({
    contact,
    telegram,
}: {
    readonly contact: Contact;
    readonly telegram: TelegramAdapter;
}) {
    const suggestions = useSuggestions(contact.id);
    const latest = suggestions.status === "success" ? suggestions.data[0] : undefined;
    if (latest === undefined) {
        return null;
    }

    const settle = async (action: "accept" | "dismiss") => {
        const result =
            action === "accept"
                ? await suggestions.acceptSuggestion(latest.id)
                : await suggestions.dismissSuggestion(latest.id);
        telegram.hapticNotification(result.error === undefined ? "success" : "error");
    };

    return (
        <Card tone="warm" aria-labelledby="home-suggestion-label">
            <div className="card__eyebrow">
                <span id="home-suggestion-label" className="card__eyebrow-label">
                    {ru.homeSuggestionLabel}
                </span>
                <span className="card__eyebrow-aside">{contact.label}</span>
            </div>
            <p className="rule-text">«{latest.text}»</p>
            <div className="row">
                <Button
                    className="row__grow"
                    onClick={() => {
                        void settle("accept");
                    }}
                >
                    {ru.suggestionAccept}
                </Button>
                <Button
                    className="row__grow"
                    variant="ghost"
                    tone="warm"
                    onClick={() => {
                        void settle("dismiss");
                    }}
                >
                    {ru.suggestionDismiss}
                </Button>
            </div>
        </Card>
    );
}

function PendingBlock({
    telegram,
    pending,
    focusPending,
}: {
    readonly telegram: TelegramAdapter;
    readonly pending: PendingRulesState;
    readonly focusPending: boolean;
}) {
    const sectionRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (focusPending && pending.status === "success" && pending.data.length > 0) {
            sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }, [focusPending, pending]);

    if (pending.status !== "success" || pending.data.length === 0) {
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
        <div ref={sectionRef} data-testid="home-pending">
            <Card tone="warm" aria-labelledby="home-pending-title">
                <h2 id="home-pending-title" className="card__title">
                    {ru.homePendingTitle}
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
                    <p className="hint-text">{ru.homePendingMore.replace("{n}", String(more))}</p>
                ) : null}
            </Card>
        </div>
    );
}

function RulesBlock({
    contact,
    onOpenContactRules,
    onAddRule,
}: {
    readonly contact: Contact;
    readonly onOpenContactRules: (contact: Contact) => void;
    readonly onAddRule: (contact: Contact) => void;
}) {
    const rules = useRules(contact.id);
    if (rules.status === "loading") {
        return <Skeleton shape="block" />;
    }
    if (rules.status === "error") {
        return <ErrorView message={rules.error.message} onRetry={rules.refetch} />;
    }

    const active = rules.data.filter((rule) => rule.status === "active");
    const authorWaiting = rules.data.find(
        (rule) =>
            rule.shared &&
            !rule.needs_my_approval &&
            (rule.status === "proposed" || rule.has_pending_edit),
    );
    const title = contact.paired
        ? ru.homeRulesPairTitle
        : ru.homeRulesSoloTitle.replace("{label}", contact.label);

    return (
        <Card aria-labelledby="home-rules-title">
            <h2 id="home-rules-title" className="card__title">
                {title}
            </h2>
            {authorWaiting !== undefined ? (
                <p className="hint-text">
                    {ru.homeAuthorWaiting.replace("{label}", contact.label)}
                </p>
            ) : null}
            {active.length === 0 ? (
                <>
                    <p className="hint-text">{ru.homeRulesEmpty}</p>
                    <Button
                        variant="outline"
                        onClick={() => {
                            onAddRule(contact);
                        }}
                    >
                        {ru.homeRulesAdd}
                    </Button>
                </>
            ) : (
                <>
                    <p className="hint-text">{homeRulesCountLabel(active.length)}</p>
                    <ul className="home-rules-preview">
                        {active.slice(0, 2).map((rule) => (
                            <li key={rule.id} className="rule-text home-rules-preview__item">
                                {oneLine(rule.text)}
                            </li>
                        ))}
                    </ul>
                    <Button
                        variant="ghost"
                        onClick={() => {
                            onOpenContactRules(contact);
                        }}
                    >
                        {ru.homeRulesAll}
                    </Button>
                </>
            )}
        </Card>
    );
}

export function HomeScreen({
    telegram,
    activeContactId,
    botUsername,
    pendingRules,
    focusPending = false,
    onOpenDecode,
    onOpenPeople,
    onOpenContactRules,
    onAddRule,
    onActivated,
    now,
}: HomeScreenProps) {
    const contacts = useContacts();
    const [pickerOpen, setPickerOpen] = useState(false);
    const greeting = greetingFor(now ?? new Date());
    const list = contacts.status === "success" ? contacts.data : [];
    const active = list.find((contact) => contact.id === activeContactId);

    const select = async (contact: Contact) => {
        setPickerOpen(false);
        if (contact.id === activeContactId) {
            return;
        }
        const result = await contacts.activateContact(contact.id);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onActivated(contact.id);
    };

    return (
        <section className="screen" aria-labelledby="home-title">
            <header className="screen-header">
                <div className="screen-header__text">
                    <p className="screen-eyebrow">{greeting}</p>
                    <h1 id="home-title" className="screen-title screen-title--hero">
                        {ru.homeTitle}
                    </h1>
                </div>
            </header>

            {contacts.status === "loading" ? <Skeleton shape="block" /> : null}
            {contacts.status === "error" ? (
                <ErrorView message={contacts.error.message} onRetry={contacts.refetch} />
            ) : null}

            {contacts.status === "success" && active !== undefined ? (
                <>
                    <Card className="contact-card">
                        <Avatar name={active.label} />
                        <div className="contact-card__body">
                            <span className="contact-card__eyebrow">{ru.homeTalkingWith}</span>
                            <span className="contact-card__name">
                                {active.label}{" "}
                                <span className="contact-card__relation">
                                    · {ru.relationships[active.relationship].toLowerCase()}
                                </span>
                            </span>
                        </div>
                        <Button
                            variant="ghost"
                            tone="accent"
                            onClick={() => {
                                setPickerOpen(true);
                            }}
                        >
                            {ru.homeChange}
                        </Button>
                    </Card>

                    <PendingBlock
                        telegram={telegram}
                        pending={pendingRules}
                        focusPending={focusPending}
                    />

                    <RulesBlock
                        contact={active}
                        onOpenContactRules={onOpenContactRules}
                        onAddRule={onAddRule}
                    />

                    <button
                        type="button"
                        className="home-decode-row"
                        onClick={onOpenDecode}
                        aria-label={`${ru.homeDecodeTitle}. ${ru.homeDecodeCta}`}
                    >
                        <ChatIcon size={22} />
                        <span className="home-decode-row__text" aria-hidden="true">
                            <span className="home-decode-row__title">{ru.homeDecodeTitle}</span>
                            <span className="home-decode-row__cta">{ru.homeDecodeCta}</span>
                        </span>
                    </button>

                    <SuggestionCard contact={active} telegram={telegram} />
                </>
            ) : null}

            {contacts.status === "success" && active === undefined ? (
                <Card>
                    <h2 className="hint-title">
                        {list.length === 0 ? ru.homeNoContactTitle : ru.homeNoActiveTitle}
                    </h2>
                    <p className="hint-text">
                        {list.length === 0 ? ru.homeNoContactText : ru.homeNoActiveText}
                    </p>
                    {list.length === 0 ? (
                        <Button variant="outline" onClick={onOpenPeople}>
                            {ru.contactsAdd}
                        </Button>
                    ) : (
                        <Button
                            variant="outline"
                            onClick={() => {
                                setPickerOpen(true);
                            }}
                        >
                            {ru.homeChoose}
                        </Button>
                    )}
                </Card>
            ) : null}

            <Card tone="dashed">
                <div className="note">
                    <KeyboardIcon size={22} />
                    <div className="stack stack--tight">
                        <span className="hint-title">{ru.homeInlineHintTitle}</span>
                        <span className="hint-text">
                            {ru.homeInlineHintBefore}
                            <b>@{botUsername}</b>
                            {ru.homeInlineHintAfter}
                        </span>
                    </div>
                </div>
            </Card>

            {pickerOpen ? (
                <ContactPicker
                    contacts={list}
                    activeContactId={activeContactId}
                    onSelect={(contact) => {
                        void select(contact);
                    }}
                    onClose={() => {
                        setPickerOpen(false);
                    }}
                />
            ) : null}
        </section>
    );
}
