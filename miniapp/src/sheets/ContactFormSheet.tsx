import { useState } from "react";

import { Button } from "../components/Button";
import { ChipGroup } from "../components/ChipGroup";
import { TextInput } from "../components/Field";
import { Sheet } from "../components/Sheet";
import type { Relationship } from "../hooks/useContacts";
import { ru, type RelationshipKey } from "../localization/ru";

const RELATIONSHIP_OPTIONS = (Object.keys(ru.relationships) as RelationshipKey[]).map((key) => ({
    value: key,
    label: ru.relationships[key],
}));

const LABEL_MAX = 32;

export type ContactFormInput = {
    readonly label: string;
    readonly relationship: Relationship;
};

export type ContactFormSheetProps = {
    readonly mode: "add" | "rename";
    readonly initialLabel?: string;
    readonly onSubmit: (input: ContactFormInput) => Promise<{ readonly error?: string }>;
    readonly onClose: () => void;
};

export function ContactFormSheet({
    mode,
    initialLabel = "",
    onSubmit,
    onClose,
}: ContactFormSheetProps) {
    const [label, setLabel] = useState(initialLabel);
    const [relationship, setRelationship] = useState<Relationship>("partner");
    const [formError, setFormError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const submit = async () => {
        const trimmed = label.trim();
        if (trimmed.length === 0 || trimmed.length > LABEL_MAX) {
            setFormError(ru.addRuleValidation);
            return;
        }
        setBusy(true);
        setFormError(null);
        const result = await onSubmit({ label: trimmed, relationship });
        setBusy(false);
        if (result.error !== undefined) {
            setFormError(result.error);
        }
    };

    return (
        <Sheet
            title={mode === "add" ? ru.contactsAdd : ru.contactsRename}
            onClose={onClose}
            footer={
                <>
                    <Button variant="ghost" onClick={onClose}>
                        {ru.cancel}
                    </Button>
                    <Button
                        disabled={busy}
                        onClick={() => {
                            void submit();
                        }}
                    >
                        {ru.save}
                    </Button>
                </>
            }
        >
            <TextInput
                label={ru.contactsLabel}
                value={label}
                maxLength={LABEL_MAX}
                onChange={setLabel}
            />
            {mode === "add" ? (
                <ChipGroup
                    legend={ru.contactsRelationship}
                    options={RELATIONSHIP_OPTIONS}
                    value={relationship}
                    onChange={setRelationship}
                />
            ) : null}
            {formError !== null ? (
                <p className="form-error" role="alert">
                    {formError}
                </p>
            ) : null}
        </Sheet>
    );
}
