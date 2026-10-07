import type { ReactNode } from "react";

import { cx } from "./cx";

export type CardTone = "surface" | "accent" | "warm" | "danger" | "sunken" | "dashed" | "outlined";

export type CardProps = {
    readonly as?: "section" | "article" | "div" | "blockquote";
    readonly tone?: CardTone;
    readonly className?: string;
    readonly "aria-labelledby"?: string;
    readonly children: ReactNode;
};

export function Card({
    as: Tag = "section",
    tone = "surface",
    className,
    children,
    ...rest
}: CardProps) {
    return (
        <Tag className={cx("card", `card--${tone}`, className)} {...rest}>
            {children}
        </Tag>
    );
}
