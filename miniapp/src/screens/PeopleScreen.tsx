import { useState } from "react";

import { Avatar } from "../components/Avatar";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { PlusIcon } from "../components/icons";
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
    readonly onOpenContact: (contact: Contact) => void;
    readonly onActivated: (contactId: string) => void;
};

type FormState = { readonly mode: "add" } | { readonly mode: "rename"; readonly target: Contact };

export function PeopleScreen({
    activeContactId,
    telegram,
    onOpenContact,
    onActivated,
}: PeopleScreenProps) {
    const contacts = useContacts();
    const [form, setForm] = useState<FormState | null>(null);

    const submit = async (input: ContactFormInput): Promise<{ error?: string }> => {
        if (form === null) {
            return {};
        }
        const result =
            form.mode === "add"
                ? await contacts.createContact(input)
                : await contacts.renameContact(form.target.id, input.label);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            if (result.error.code === "contact_limit") {
                return { error: ru.contactsLimit };
            }
            return { error: result.error.message || ru.errorGeneric };
        }
        telegram.hapticNotification("success");
        setForm(null);
        return {};
    };

    const activate = async (contactId: string) => {
        const result = await contacts.activateContact(contactId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onActivated(contactId);
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
                                        {contact.paired ? (
                                            <Badge tone="warm">{ru.contactPairedBadge}</Badge>
                                        ) : null}
                                    </button>
                                    <div className="person__actions">
                                        <Button
                                            variant="ghost"
                                            onClick={() => {
                                                setForm({ mode: "rename", target: contact });
                                            }}
                                        >
                                            {ru.contactsRename}
                                        </Button>
                                        {!isActive ? (
                                            <Button
                                                variant="ghost"
                                                tone="accent"
                                                onClick={() => {
                                                    void activate(contact.id);
                                                }}
                                            >
                                                {ru.contactsMakeActive}
                                            </Button>
                                        ) : null}
                                    </div>
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
                        setForm({ mode: "add" });
                    }}
                >
                    <PlusIcon size={20} />
                    {ru.contactsAdd}
                </Button>
            </div>

            {form !== null ? (
                <ContactFormSheet
                    mode={form.mode}
                    {...(form.mode === "rename" ? { initialLabel: form.target.label } : {})}
                    onSubmit={submit}
                    onClose={() => {
                        setForm(null);
                    }}
                />
            ) : null}
        </section>
    );
}
