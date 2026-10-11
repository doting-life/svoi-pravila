import { ru } from "../localization/ru";
import type { Contact } from "../hooks/useContacts";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { Sheet } from "./Sheet";

export type ContactPickerProps = {
    readonly contacts: readonly Contact[];
    readonly activeContactId: string | null | undefined;
    readonly onSelect: (contact: Contact) => void;
    readonly onClose: () => void;
    readonly noneOption?: {
        readonly label: string;
        readonly selected: boolean;
        readonly onSelect: () => void;
    };
};

export function ContactPicker({
    contacts,
    activeContactId,
    onSelect,
    onClose,
    noneOption,
}: ContactPickerProps) {
    return (
        <Sheet
            title={ru.pickerTitle}
            onClose={onClose}
            footer={
                <Button variant="ghost" onClick={onClose}>
                    {ru.cancel}
                </Button>
            }
        >
            {contacts.length === 0 && noneOption === undefined ? (
                <p className="muted">{ru.pickerEmpty}</p>
            ) : null}
            <div className="picker">
                {noneOption !== undefined ? (
                    <button
                        type="button"
                        className="picker__item"
                        aria-current={noneOption.selected ? "true" : undefined}
                        onClick={() => {
                            noneOption.onSelect();
                        }}
                    >
                        <span className="picker__text">
                            <span className="person__name">{noneOption.label}</span>
                        </span>
                    </button>
                ) : null}
                {contacts.map((contact) => (
                    <button
                        key={contact.id}
                        type="button"
                        className="picker__item"
                        aria-current={contact.id === activeContactId ? "true" : undefined}
                        onClick={() => {
                            onSelect(contact);
                        }}
                    >
                        <Avatar name={contact.label} />
                        <span className="picker__text">
                            <span className="person__name">{contact.label}</span>
                            <span className="person__meta">
                                {ru.relationships[contact.relationship]}
                            </span>
                        </span>
                    </button>
                ))}
            </div>
        </Sheet>
    );
}
