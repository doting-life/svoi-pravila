import { cx } from "./cx";

export type SkeletonProps = {
    readonly shape?: "line" | "block" | "circle";
    readonly short?: boolean;
    readonly className?: string;
};

export function Skeleton({ shape = "line", short = false, className }: SkeletonProps) {
    return (
        <span
            className={cx("skeleton", `skeleton--${shape}`, short && "skeleton--short", className)}
            aria-hidden="true"
        />
    );
}
