import { useState } from "react";

import { Avatar } from "../components/Avatar";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ContactPicker } from "../components/ContactPicker";
import { ChatIcon, KeyboardIcon } from "../components/icons";
import { Skeleton } from "../components/Skeleton";
import { ErrorView } from "../components/StatusViews";
import { greetingFor } from "../home/greeting";
import { useContacts, type Contact } from "../hooks/useContacts";
import { useSuggestions } from "../hooks/useSuggestions";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type HomeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly activeContactId: string | null | undefined;
    readonly botUsername: string;
    readonly onOpenDecode: () => void;
    readonly onOpenPeople: () => void;
    readonly onActivated: (contactId: string) => void;
    readonly now?: Date;
};

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

export function HomeScreen({
    telegram,
    activeContactId,
    botUsername,
    onOpenDecode,
    onOpenPeople,
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

                    <Card tone="accent" aria-labelledby="home-decode-title">
                        <div className="card__heading-row">
                            <ChatIcon size={24} />
                            <h2 id="home-decode-title" className="card__title">
                                {ru.homeDecodeTitle}
                            </h2>
                        </div>
                        <p className="card__text">{ru.homeDecodeText}</p>
                        <Button className="btn--on-accent" onClick={onOpenDecode}>
                            {ru.homeDecodeCta}
                        </Button>
                    </Card>

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
