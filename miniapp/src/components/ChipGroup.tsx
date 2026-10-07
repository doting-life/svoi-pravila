import { useId } from "react";

import { hapticSelection } from "../telegram/webapp";

export type ChipOption<T extends string> = {
    readonly value: T;
    readonly label: string;
};

export type ChipGroupProps<T extends string> = {
    readonly legend: string;
    readonly options: readonly ChipOption<T>[];
    readonly value: T;
    readonly onChange: (value: T) => void;
};

export function ChipGroup<T extends string>({
    legend,
    options,
    value,
    onChange,
}: ChipGroupProps<T>) {
    const name = useId();
    return (
        <fieldset className="choice-group">
            <legend className="choice-group__legend">{legend}</legend>
            <div className="chips">
                {options.map((option) => (
                    <label key={option.value} className="chip">
                        <input
                            type="radio"
                            className="choice-input"
                            name={name}
                            value={option.value}
                            checked={option.value === value}
                            onChange={() => {
                                hapticSelection();
                                onChange(option.value);
                            }}
                        />
                        <span className="chip__label">{option.label}</span>
                    </label>
                ))}
            </div>
        </fieldset>
    );
}
