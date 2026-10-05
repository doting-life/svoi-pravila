import { useState } from "react";

import { EmptyView, ErrorView, LoadingView } from "../components/StatusViews";
import type { Contact, Relationship } from "../hooks/useContacts";
import { useContacts } from "../hooks/useContacts";
import { ru, type RelationshipKey } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

const RELATIONSHIPS = Object.keys(ru.relationships) as RelationshipKey[];

export type ContactsScreenProps = {
    readonly activeContactId: string | null | undefined;
    readonly telegram: TelegramAdapter;
    readonly onOpenContact: (contactId: string) => void;
    readonly onActivated: () => void;
};

type ModalKind = "add" | "rename" | null;

export function ContactsScreen({
    activeContactId,
    telegram,
    onOpenContact,
    onActivated,
}: ContactsScreenProps) {
    const contacts = useContacts();
    const [modal, setModal] = useState<ModalKind>(null);
    const [renameTarget, setRenameTarget] = useState<Contact | null>(null);
    const [label, setLabel] = useState("");
    const [relationship, setRelationship] = useState<Relationship>("partner");
    const [formError, setFormError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const openAdd = () => {
        setLabel("");
        setRelationship("partner");
        setFormError(null);
        setRenameTarget(null);
        setModal("add");
    };

    const openRename = (contact: Contact) => {
        setLabel(contact.label);
        setFormError(null);
        setRenameTarget(contact);
        setModal("rename");
    };

    const closeModal = () => {
        setModal(null);
        setRenameTarget(null);
        setFormError(null);
    };

    const submitModal = async () => {
        const trimmed = label.trim();
        if (trimmed.length === 0 || trimmed.length > 32) {
            setFormError(ru.addRuleValidation);
            return;
        }
        setBusy(true);
        setFormError(null);
        if (modal === "add") {
            const result = await contacts.createContact({ label: trimmed, relationship });
            setBusy(false);
            if (result.error !== undefined) {
                if (result.error.code === "contact_limit") {
                    setFormError(ru.contactsLimit);
                } else {
                    setFormError(result.error.message || ru.errorGeneric);
                }
                telegram.hapticNotification("error");
                return;
            }
            telegram.hapticNotification("success");
            closeModal();
            return;
        }
        if (modal === "rename" && renameTarget !== null) {
            const result = await contacts.renameContact(renameTarget.id, trimmed);
            setBusy(false);
            if (result.error !== undefined) {
                setFormError(result.error.message || ru.errorGeneric);
                telegram.hapticNotification("error");
                return;
            }
            telegram.hapticNotification("success");
            closeModal();
            return;
        }
        setBusy(false);
    };

    const activate = async (contactId: string) => {
        const result = await contacts.activateContact(contactId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onActivated();
    };

    return (
        <section className="screen" aria-labelledby="contacts-title">
            <header className="screen-header">
                <h2 id="contacts-title" className="screen-title">
                    {ru.contactsTitle}
                </h2>
                <button type="button" className="btn btn-primary" onClick={openAdd}>
                    {ru.contactsAdd}
                </button>
            </header>

            {contacts.status === "loading" ? <LoadingView /> : null}
            {contacts.status === "error" ? (
                <ErrorView message={contacts.error.message} onRetry={contacts.refetch} />
            ) : null}
            {contacts.status === "success" && contacts.data.length === 0 ? (
                <EmptyView message={ru.contactsEmpty} />
            ) : null}
            {contacts.status === "success" && contacts.data.length > 0 ? (
                <ul className="list">
                    {contacts.data.map((contact) => {
                        const isActive = contact.id === activeContactId;
                        return (
                            <li key={contact.id} className="list-item">
                                <button
                                    type="button"
                                    className="list-item-main"
                                    onClick={() => {
                                        onOpenContact(contact.id);
                                    }}
                                >
                                    <span className="list-item-title">{contact.label}</span>
                                    <span className="list-item-meta">
                                        {ru.relationships[contact.relationship]}
                                        {isActive ? ` · ${ru.contactsActiveBadge}` : ""}
                                    </span>
                                </button>
                                <div className="list-item-actions">
                                    <button
                                        type="button"
                                        className="btn btn-secondary"
                                        onClick={() => {
                                            openRename(contact);
                                        }}
                                    >
                                        {ru.contactsRename}
                                    </button>
                                    {!isActive ? (
                                        <button
                                            type="button"
                                            className="btn btn-secondary"
                                            onClick={() => {
                                                void activate(contact.id);
                                            }}
                                        >
                                            {ru.contactsMakeActive}
                                        </button>
                                    ) : null}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            ) : null}

            {modal !== null ? (
                <div
                    className="modal"
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="contact-form-title"
                >
                    <div className="modal-panel">
                        <h3 id="contact-form-title" className="screen-title">
                            {modal === "add" ? ru.contactsAdd : ru.contactsRename}
                        </h3>
                        <label className="field">
                            <span className="field-label">{ru.contactsLabel}</span>
                            <input
                                className="field-input"
                                value={label}
                                maxLength={32}
                                onChange={(event) => {
                                    setLabel(event.target.value);
                                }}
                            />
                            <span className="field-counter" aria-live="polite">
                                {label.length}/32
                            </span>
                        </label>
                        {modal === "add" ? (
                            <fieldset className="field">
                                <legend className="field-label">{ru.contactsRelationship}</legend>
                                <div className="choice-row">
                                    {RELATIONSHIPS.map((key) => (
                                        <label key={key} className="choice">
                                            <input
                                                type="radio"
                                                name="relationship"
                                                value={key}
                                                checked={relationship === key}
                                                onChange={() => {
                                                    setRelationship(key);
                                                }}
                                            />
                                            <span>{ru.relationships[key]}</span>
                                        </label>
                                    ))}
                                </div>
                            </fieldset>
                        ) : null}
                        {formError !== null ? (
                            <p className="status-message" role="alert">
                                {formError}
                            </p>
                        ) : null}
                        <div className="modal-actions">
                            <button
                                type="button"
                                className="btn btn-secondary"
                                onClick={closeModal}
                            >
                                {ru.cancel}
                            </button>
                            <button
                                type="button"
                                className="btn btn-primary"
                                disabled={busy}
                                onClick={() => {
                                    void submitModal();
                                }}
                            >
                                {ru.save}
                            </button>
                        </div>
                    </div>
                </div>
            ) : null}
        </section>
    );
}
