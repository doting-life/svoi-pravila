import { useState } from "react";

import { Avatar } from "../components/Avatar";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ChevronRightIcon, PlusIcon } from "../components/icons";
import { ScreenHeader } from "../components/ScreenHeader";
import { EmptyView, ErrorView, LoadingView } from "../components/StatusViews";
import type { Contact } from "../hooks/useContacts";
import { useContacts } from "../hooks/useContacts";
import { ru } from "../localization/ru";
import { ContactFormSheet, type ContactFormInput } from "../sheets/ContactFormSheet";
import type { TelegramAdapter } from "../telegram/webapp";

export type PeopleScreenProps = {
    readonly activeContactId: string | null | undefined;
    readonly telegram: TelegramAdapter;
    readonly pendingByContact?: Readonly<Record<string, number>>;
    readonly onOpenContact: (contact: Contact) => void;
};

export function PeopleScreen({
    activeContactId,
    telegram,
    pendingByContact = {},
    onOpenContact,
}: PeopleScreenProps) {
    const contacts = useContacts();
    const [adding, setAdding] = useState(false);

    const submit = async (input: ContactFormInput): Promise<{ error?: string }> => {
        const result = await contacts.createContact(input);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            if (result.error.code === "contact_limit") {
                return { error: ru.contactsLimit };
            }
            return { error: result.error.message || ru.errorGeneric };
        }
        telegram.hapticNotification("success");
        setAdding(false);
        return {};
    };

    return (
        <section className="screen" aria-labelledby="contacts-title">
            <ScreenHeader
                title={ru.contactsTitle}
                titleId="contacts-title"
                lead={ru.contactsLead}
            />

            {contacts.status === "loading" ? <LoadingView /> : null}
            {contacts.status === "error" ? (
                <ErrorView message={contacts.error.message} onRetry={contacts.refetch} />
            ) : null}
            {contacts.status === "success" && contacts.data.length === 0 ? (
                <EmptyView message={ru.contactsEmpty} />
            ) : null}
            {contacts.status === "success" && contacts.data.length > 0 ? (
                <ul className="people-list">
                    {contacts.data.map((contact) => {
                        const isActive = contact.id === activeContactId;
                        const pendingCount = pendingByContact[contact.id] ?? 0;
                        return (
                            <li key={contact.id}>
                                <Card className="person">
                                    <button
                                        type="button"
                                        className="person__open"
                                        onClick={() => {
                                            onOpenContact(contact);
                                        }}
                                    >
                                        <Avatar name={contact.label} />
                                        <span className="person__text">
                                            <span className="person__name">{contact.label}</span>
                                            <span className="person__meta">
                                                {ru.relationships[contact.relationship]}
                                                {isActive ? ` · ${ru.contactsActiveBadge}` : ""}
                                            </span>
                                        </span>
                                        {pendingCount > 0 ? (
                                            <Badge tone="warm" className="person__pending-badge">
                                                {pendingCount}
                                            </Badge>
                                        ) : null}
                                        {contact.paired ? (
                                            <Badge tone="warm">{ru.contactPairedBadge}</Badge>
                                        ) : null}
                                        <ChevronRightIcon size={20} className="person__chevron" />
                                    </button>
                                </Card>
                            </li>
                        );
                    })}
                </ul>
            ) : null}

            <div className="screen-footer">
                <Button
                    variant="outline"
                    size="lg"
                    block
                    onClick={() => {
                        setAdding(true);
                    }}
                >
                    <PlusIcon size={20} />
                    {ru.contactsAdd}
                </Button>
            </div>

            {adding ? (
                <ContactFormSheet
                    mode="add"
                    onSubmit={submit}
                    onClose={() => {
                        setAdding(false);
                    }}
                />
            ) : null}
        </section>
    );
}
