import { cx } from "./cx";

export type AvatarProps = {
    readonly name: string;
    readonly size?: "sm" | "md" | "lg";
    readonly className?: string;
};

export function Avatar({ name, size = "md", className }: AvatarProps) {
    const initial = Array.from(name.trim())[0]?.toUpperCase() ?? "";
    return (
        <span className={cx("avatar", `avatar--${size}`, className)} aria-hidden="true">
            {initial}
        </span>
    );
}
