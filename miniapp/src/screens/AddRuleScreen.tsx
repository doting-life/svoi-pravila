import { useState } from "react";

import { Button } from "../components/Button";
import { ChipGroup } from "../components/ChipGroup";
import { TextArea } from "../components/Field";
import { SegmentedControl } from "../components/SegmentedControl";
import { useRules, type RuleCategory } from "../hooks/useRules";
import { ru, type CategoryKey } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

const CATEGORY_OPTIONS = (Object.keys(ru.categories) as CategoryKey[]).map((key) => ({
    value: key,
    label: ru.categories[key],
}));

const SCOPE_OPTIONS = [
    { value: "personal", label: ru.addRuleScopePersonal },
    { value: "shared", label: ru.addRuleScopeShared },
] as const;

const RULE_TEXT_MAX = 280;

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
    const rules = useRules(contactId);
    const [category, setCategory] = useState<RuleCategory>(initialCategory);
    const [text, setText] = useState(initialText);
    const [scope, setScope] = useState<"personal" | "shared">("personal");
    const [formError, setFormError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const shared = paired && scope === "shared";

    const submit = async () => {
        const trimmed = text.trim();
        if (trimmed.length === 0 || trimmed.length > RULE_TEXT_MAX) {
            setFormError(ru.addRuleValidation);
            return;
        }
        setBusy(true);
        setFormError(null);
        const result = await rules.createRule({ category, text: trimmed, shared });
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
                        onChange={setScope}
                    />
                    {shared ? <p className="hint-text">{ru.addRuleSharedNote}</p> : null}
                </>
            ) : null}

            <ChipGroup
                legend={ru.addRuleCategory}
                options={CATEGORY_OPTIONS}
                value={category}
                onChange={setCategory}
            />

            <TextArea
                label={ru.addRuleText}
                value={text}
                maxLength={RULE_TEXT_MAX}
                disabled={busy}
                footer={ru.addRuleHint}
                large
                onChange={setText}
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
                    {shared ? ru.addRuleSubmitShared : ru.addRuleSubmit}
                </Button>
            </div>
        </section>
    );
}
