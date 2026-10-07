import { useId } from "react";

import { hapticSelection } from "../telegram/webapp";
import type { ChipOption } from "./ChipGroup";

export type SegmentedControlProps<T extends string> = {
    readonly legend: string;
    readonly options: readonly ChipOption<T>[];
    readonly value: T;
    readonly onChange: (value: T) => void;
};

export function SegmentedControl<T extends string>({
    legend,
    options,
    value,
    onChange,
}: SegmentedControlProps<T>) {
    const name = useId();
    return (
        <fieldset className="choice-group">
            <legend className="choice-group__legend">{legend}</legend>
            <div className="segmented">
                {options.map((option) => (
                    <label key={option.value} className="segmented__item">
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
                        <span className="segmented__label">{option.label}</span>
                    </label>
                ))}
            </div>
        </fieldset>
    );
}
