import { ru } from "../localization/ru";

export type ProgressStepsProps = {
    readonly current: number;
    readonly total: number;
};

export function ProgressSteps({ current, total }: ProgressStepsProps) {
    const label = ru.stepOf.replace("{current}", String(current)).replace("{total}", String(total));
    return (
        <div
            className="progress-steps"
            role="progressbar"
            aria-label={label}
            aria-valuemin={1}
            aria-valuemax={total}
            aria-valuenow={current}
            aria-valuetext={label}
        >
            {Array.from({ length: total }, (_, index) => (
                <span
                    key={index}
                    className={
                        index < current ? "progress-steps__bar is-done" : "progress-steps__bar"
                    }
                />
            ))}
        </div>
    );
}
