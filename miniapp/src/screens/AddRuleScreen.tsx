import { useState } from "react";

import { useRules, type RuleCategory } from "../hooks/useRules";
import { ru, type CategoryKey } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

const CATEGORIES = Object.keys(ru.categories) as CategoryKey[];

export type AddRuleScreenProps = {
    readonly contactId: string;
    readonly paired: boolean;
    readonly telegram: TelegramAdapter;
    readonly onCreated: () => void;
    readonly initialCategory?: RuleCategory;
    readonly initialText?: string;
};

export function AddRuleScreen({
    contactId,
    paired,
    telegram,
    onCreated,
    initialCategory = "other",
    initialText = "",
}: AddRuleScreenProps) {
    const rules = useRules(contactId);
    const [category, setCategory] = useState<RuleCategory>(initialCategory);
    const [text, setText] = useState(initialText);
    const [shared, setShared] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const submit = async () => {
        const trimmed = text.trim();
        if (trimmed.length === 0 || trimmed.length > 280) {
            setFormError(ru.addRuleValidation);
            return;
        }
        setBusy(true);
        setFormError(null);
        const result = await rules.createRule({
            category,
            text: trimmed,
            shared: paired ? shared : false,
        });
        setBusy(false);
        if (result.error !== undefined) {
            if (result.error.code === "open_rule_limit") {
                setFormError(ru.addRuleOpenLimit);
            } else if (
                result.error.code === "validation_error" ||
                result.error.kind === "validation"
            ) {
                setFormError(ru.addRuleValidation);
            } else {
                setFormError(result.error.message || ru.errorGeneric);
            }
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onCreated();
    };

    return (
        <section className="screen" aria-labelledby="add-rule-title">
            <h2 id="add-rule-title" className="screen-title">
                {ru.addRuleTitle}
            </h2>

            {paired ? (
                <fieldset className="field">
                    <legend className="field-label">{ru.addRuleScope}</legend>
                    <div className="choice-row">
                        <label className="choice">
                            <input
                                type="radio"
                                name="scope"
                                checked={!shared}
                                onChange={() => {
                                    setShared(false);
                                }}
                            />
                            <span>{ru.addRuleScopePersonal}</span>
                        </label>
                        <label className="choice">
                            <input
                                type="radio"
                                name="scope"
                                checked={shared}
                                onChange={() => {
                                    setShared(true);
                                }}
                            />
                            <span>{ru.addRuleScopeShared}</span>
                        </label>
                    </div>
                </fieldset>
            ) : null}

            <fieldset className="field">
                <legend className="field-label">{ru.addRuleCategory}</legend>
                <div className="choice-row">
                    {CATEGORIES.map((key) => (
                        <label key={key} className="choice">
                            <input
                                type="radio"
                                name="category"
                                value={key}
                                checked={category === key}
                                onChange={() => {
                                    setCategory(key);
                                }}
                            />
                            <span>{ru.categories[key]}</span>
                        </label>
                    ))}
                </div>
            </fieldset>

            <label className="field">
                <span className="field-label">{ru.addRuleText}</span>
                <textarea
                    className="field-input field-textarea"
                    value={text}
                    maxLength={280}
                    rows={5}
                    onChange={(event) => {
                        setText(event.target.value);
                    }}
                />
                <span className="field-counter" aria-live="polite">
                    {text.length}/280
                </span>
            </label>

            {formError !== null ? (
                <p className="status-message" role="alert">
                    {formError}
                </p>
            ) : null}

            <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => {
                    void submit();
                }}
            >
                {ru.addRuleSubmit}
            </button>
        </section>
    );
}
