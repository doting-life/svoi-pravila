import { useId, type ReactNode } from "react";

import { cx } from "./cx";

type FieldBase = {
    readonly label: string;
    readonly value: string;
    readonly onChange: (value: string) => void;
    readonly maxLength: number;
    readonly disabled?: boolean;
    readonly placeholder?: string;
    readonly footer?: ReactNode;
};

export type TextAreaProps = FieldBase & {
    readonly rows?: number;
    readonly large?: boolean;
};

export type TextInputProps = FieldBase;

function FieldFooter({
    id,
    footer,
    length,
    maxLength,
}: {
    readonly id: string;
    readonly footer: ReactNode;
    readonly length: number;
    readonly maxLength: number;
}) {
    return (
        <div className="field__footer">
            <span id={`${id}-footer`} className="field__hint">
                {footer}
            </span>
            <span className="field__counter" aria-live="polite">
                {length} / {maxLength}
            </span>
        </div>
    );
}

export function TextArea({
    label,
    value,
    onChange,
    maxLength,
    disabled = false,
    placeholder,
    footer,
    rows = 5,
    large = false,
}: TextAreaProps) {
    const id = useId();
    return (
        <div className="field">
            <label className="field__label" htmlFor={id}>
                {label}
            </label>
            <textarea
                id={id}
                className={cx(
                    "field__control",
                    "field__control--area",
                    large && "field__control--large",
                )}
                value={value}
                maxLength={maxLength}
                rows={rows}
                disabled={disabled}
                placeholder={placeholder}
                onChange={(event) => {
                    onChange(event.target.value);
                }}
            />
            <FieldFooter id={id} footer={footer} length={value.length} maxLength={maxLength} />
        </div>
    );
}

export function TextInput({
    label,
    value,
    onChange,
    maxLength,
    disabled = false,
    placeholder,
    footer,
}: TextInputProps) {
    const id = useId();
    return (
        <div className="field">
            <label className="field__label" htmlFor={id}>
                {label}
            </label>
            <input
                id={id}
                className="field__control"
                value={value}
                maxLength={maxLength}
                disabled={disabled}
                placeholder={placeholder}
                onChange={(event) => {
                    onChange(event.target.value);
                }}
            />
            <FieldFooter id={id} footer={footer} length={value.length} maxLength={maxLength} />
        </div>
    );
}
