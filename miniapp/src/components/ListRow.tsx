import { useId, type ReactNode } from "react";

import { cx } from "./cx";

export type ListRowProps = {
    readonly icon?: ReactNode;
    readonly title: string;
    readonly subtitle?: string;
    readonly trailing?: ReactNode;
    readonly disabled?: boolean;
    readonly onClick: () => void;
};

export function ListRow({
    icon,
    title,
    subtitle,
    trailing,
    disabled = false,
    onClick,
}: ListRowProps) {
    const titleId = useId();
    const subtitleId = useId();
    return (
        <button
            type="button"
            className={cx("list-row", icon === undefined && "list-row--plain")}
            aria-labelledby={titleId}
            aria-describedby={subtitle !== undefined ? subtitleId : undefined}
            disabled={disabled}
            onClick={onClick}
        >
            {icon !== undefined ? <span className="list-row__icon">{icon}</span> : null}
            <span className="list-row__body">
                <span id={titleId} className="list-row__title">
                    {title}
                </span>
                {subtitle !== undefined ? (
                    <span id={subtitleId} className="list-row__subtitle">
                        {subtitle}
                    </span>
                ) : null}
            </span>
            {trailing !== undefined ? <span className="list-row__trailing">{trailing}</span> : null}
        </button>
    );
}

export function ListGroup({ children }: { readonly children: ReactNode }) {
    return <div className="list-group">{children}</div>;
}
