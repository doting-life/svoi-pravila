import { useState } from "react";

import { Button } from "../components/Button";
import { ChipGroup } from "../components/ChipGroup";
import { TextArea } from "../components/Field";
import { SegmentedControl } from "../components/SegmentedControl";
import { useMe } from "../hooks/useMe";
import { useRules, type RuleCategory } from "../hooks/useRules";
import { ru, type CategoryKey } from "../localization/ru";
import { codePointLength, validateRuleText } from "../rules/ruleText";
import type { TelegramAdapter } from "../telegram/webapp";

const CATEGORY_OPTIONS = (Object.keys(ru.categories) as CategoryKey[]).map((key) => ({
    value: key,
    label: ru.categories[key],
}));

const SCOPE_OPTIONS = [
    { value: "personal", label: ru.addRuleScopePersonal },
    { value: "shared", label: ru.addRuleScopeShared },
] as const;

export type AddRuleScreenProps = {
    readonly contactId: string;
    readonly contactLabel: string;
    readonly paired: boolean;
    readonly telegram: TelegramAdapter;
    readonly onCreated: () => void;
    readonly initialCategory?: RuleCategory;
    readonly initialText?: string;
};

export function AddRuleScreen({
    contactId,
    contactLabel,
    paired,
    telegram,
    onCreated,
    initialCategory = "other",
    initialText = "",
}: AddRuleScreenProps) {
    const me = useMe();
    const maxChars = me.status === "success" ? me.data.rule_text_max_chars : 500;
    const rules = useRules(contactId);
    const [category, setCategory] = useState<RuleCategory>(initialCategory);
    const [text, setText] = useState(initialText);
    const [scope, setScope] = useState<"personal" | "shared">("personal");
    const [formError, setFormError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const shared = paired && scope === "shared";

    const clearError = () => {
        setFormError(null);
    };

    const errorForClient = (error: "empty" | "too_long" | "invalid_chars", actual: number) => {
        if (error === "empty") {
            return ru.addRuleEmpty;
        }
        if (error === "invalid_chars") {
            return ru.addRuleInvalidChars;
        }
        return ru.addRuleTooLong
            .replace("{n}", String(actual))
            .replace("{max}", String(maxChars))
            .replace("{over}", String(actual - maxChars));
    };

    const submit = async () => {
        const checked = validateRuleText(text, maxChars);
        if (!checked.ok) {
            setFormError(errorForClient(checked.error, checked.actual));
            return;
        }
        setBusy(true);
        setFormError(null);
        const result = await rules.createRule({
            category,
            text: checked.value,
            shared,
        });
        setBusy(false);
        if (result.error !== undefined) {
            if (result.error.code === "open_rule_limit") {
                setFormError(ru.addRuleOpenLimit);
            } else if (result.error.code === "rule_text_empty") {
                setFormError(ru.addRuleEmpty);
            } else if (result.error.code === "rule_text_invalid_chars") {
                setFormError(ru.addRuleInvalidChars);
            } else if (result.error.code === "rule_text_too_long") {
                const actual = result.error.actual ?? codePointLength(text);
                const max = result.error.max ?? maxChars;
                setFormError(
                    ru.addRuleTooLong
                        .replace("{n}", String(actual))
                        .replace("{max}", String(max))
                        .replace("{over}", String(actual - max)),
                );
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
            <header className="screen-header">
                <div className="screen-header__text">
                    <p className="screen-eyebrow">
                        {ru.addRuleFor.replace("{label}", contactLabel)}
                    </p>
                    <h1 id="add-rule-title" className="screen-title">
                        {ru.addRuleTitle}
                    </h1>
                </div>
            </header>

            {paired ? (
                <>
                    <SegmentedControl
                        legend={ru.addRuleScope}
                        options={SCOPE_OPTIONS}
                        value={scope}
                        onChange={(next) => {
                            clearError();
                            setScope(next);
                        }}
                    />
                    <p className="hint-text">
                        {shared
                            ? ru.addRuleHintShared.replace("{label}", contactLabel)
                            : ru.addRuleHintPersonal}
                    </p>
                </>
            ) : null}

            <ChipGroup
                legend={ru.addRuleCategory}
                options={CATEGORY_OPTIONS}
                value={category}
                onChange={(next) => {
                    clearError();
                    setCategory(next);
                }}
            />

            <TextArea
                label={ru.addRuleText}
                value={text}
                maxLength={maxChars}
                countCodePoints
                disabled={busy}
                footer={ru.addRuleHint}
                large
                onChange={(next) => {
                    clearError();
                    setText(next);
                }}
            />

            {formError !== null ? (
                <p className="form-error" role="alert">
                    {formError}
                </p>
            ) : null}

            <div className="screen-footer">
                <Button
                    size="lg"
                    block
                    disabled={busy}
                    onClick={() => {
                        void submit();
                    }}
                >
                    {shared
                        ? ru.addRuleSubmitShared.replace("{label}", contactLabel)
                        : ru.addRuleSubmit}
                </Button>
            </div>
        </section>
    );
}
