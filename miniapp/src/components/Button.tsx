import type { ButtonHTMLAttributes, MouseEvent, ReactNode } from "react";

import { hapticImpact } from "../telegram/webapp";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "outline" | "ghost" | "danger";
export type ButtonTone = "default" | "accent" | "warm" | "danger";

export type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> & {
    readonly variant?: ButtonVariant;
    readonly tone?: ButtonTone;
    readonly size?: "md" | "lg";
    readonly block?: boolean;
    readonly bare?: boolean;
    readonly className?: string;
    readonly children: ReactNode;
};

export function Button({
    variant = "primary",
    tone = "default",
    size = "md",
    block = false,
    bare = false,
    className,
    type = "button",
    onClick,
    children,
    ...rest
}: ButtonProps) {
    const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
        hapticImpact("light");
        onClick?.(event);
    };
    return (
        <button
            {...rest}
            type={type}
            className={cx(
                "btn",
                `btn--${variant}`,
                variant === "ghost" && `btn--tone-${tone}`,
                size === "lg" && "btn--lg",
                block && "btn--block",
                bare && "btn--bare",
                className,
            )}
            onClick={handleClick}
        >
            {children}
        </button>
    );
}
