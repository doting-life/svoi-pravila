import type { ReactNode } from "react";

import { cx } from "./cx";

export type BadgeTone = "accent" | "warm" | "neutral";

export type BadgeProps = {
    readonly tone?: BadgeTone;
    readonly className?: string;
    readonly children: ReactNode;
};

export function Badge({ tone = "neutral", className, children }: BadgeProps) {
    return <span className={cx("badge", `badge--${tone}`, className)}>{children}</span>;
}
