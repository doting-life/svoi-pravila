import { useState } from "react";

import { Avatar } from "../components/Avatar";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ContactPicker } from "../components/ContactPicker";
import { TextArea } from "../components/Field";
import { ChevronDownIcon, ClipboardIcon, KeyboardIcon } from "../components/icons";
import { hapticSelection, readClipboardText } from "../telegram/webapp";
import { useContacts } from "../hooks/useContacts";
import { ru } from "../localization/ru";
import type { ComposeMode } from "../navigation/stack";

export type HomeMode = ComposeMode | "decode";

export type HomeSubmission = {
    readonly mode: HomeMode;
    readonly draft: string;
    readonly contactId: string | null;
};

export type HomeScreenProps = {
    readonly botUsername: string;
    readonly textMax: number;
    readonly onSubmit: (submission: HomeSubmission) => void;
};

const MODES: readonly {
    readonly value: HomeMode;
    readonly title: string;
    readonly hint: string;
}[] = [
    { value: "check", title: ru.homeModeCheck, hint: ru.homeModeCheckHint },
    { value: "soften", title: ru.homeModeSoften, hint: ru.homeModeSoftenHint },
    { value: "decode", title: ru.homeModeDecode, hint: ru.homeModeDecodeHint },
];

function sliceCodePoints(text: string, max: number): string {
    return Array.from(text).slice(0, max).join("");
}

/**
 * Home: pick what to do with a message, optionally for a contact, and type the text.
 * Mode, contact and text are local to this screen and are never persisted.
 */
export function HomeScreen({ botUsername, textMax, onSubmit }: HomeScreenProps) {
    const contacts = useContacts();
    const [mode, setMode] = useState<HomeMode>("check");
    const [contactId, setContactId] = useState<string | null>(null);
    const [draft, setDraft] = useState("");
    const [pickerOpen, setPickerOpen] = useState(false);
    const list = contacts.status === "success" ? contacts.data : [];
    const selected = list.find((contact) => contact.id === contactId);
    const decoding = mode === "decode";
    const empty = draft.trim().length === 0;

    const paste = async () => {
        const text = await readClipboardText();
        if (text !== null) {
            setDraft(sliceCodePoints(text, textMax));
        }
    };

    return (
        <section className="screen" aria-labelledby="home-title">
            <header className="screen-header">
                <div className="screen-header__text">
                    <h1 id="home-title" className="screen-title screen-title--hero">
                        {ru.homeTitle}
                    </h1>
                </div>
            </header>

            <div className="mode-cards" role="radiogroup" aria-labelledby="home-title">
                {MODES.map((item) => (
                    <label key={item.value} className="mode-card">
                        <input
                            type="radio"
                            className="choice-input"
                            name="home-mode"
                            value={item.value}
                            checked={item.value === mode}
                            onChange={() => {
                                hapticSelection();
                                setMode(item.value);
                            }}
                        />
                        <span className="mode-card__body">
                            <span className="mode-card__title">{item.title}</span>
                            <span className="mode-card__hint">{item.hint}</span>
                        </span>
                    </label>
                ))}
            </div>

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
                        ? ru.homeForWhom.replace("{label}", selected.label)
                        : ru.homeForWhomNone}
                </span>
                <ChevronDownIcon size={18} />
            </button>

            <TextArea
                label={decoding ? ru.homeIncomingLabel : ru.homeDraftLabel}
                value={draft}
                maxLength={textMax}
                countCodePoints
                placeholder={decoding ? ru.decodePlaceholder : ru.composePlaceholder}
                rows={7}
                large
                footer={
                    <>
                        {ru.homeNote} ·{" "}
                        <button
                            type="button"
                            className="paste-button"
                            onClick={() => {
                                void paste();
                            }}
                        >
                            <ClipboardIcon size={18} />
                            {ru.homePaste}
                        </button>
                    </>
                }
                onChange={(next) => {
                    setDraft(sliceCodePoints(next, textMax));
                }}
            />

            <Button
                size="lg"
                block
                disabled={empty}
                onClick={() => {
                    onSubmit({ mode, draft: draft.trim(), contactId });
                }}
            >
                {decoding ? ru.homeSubmitDecode : ru.homeSubmitVariants}
            </Button>

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
                    selectedContactId={contactId}
                    noneOption={{
                        label: ru.pickerNone,
                        selected: contactId === null,
                        onSelect: () => {
                            setPickerOpen(false);
                            setContactId(null);
                        },
                    }}
                    onSelect={(contact) => {
                        setPickerOpen(false);
                        setContactId(contact.id);
                    }}
                    onClose={() => {
                        setPickerOpen(false);
                    }}
                />
            ) : null}
        </section>
    );
}
