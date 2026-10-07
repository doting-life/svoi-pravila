import type { ReactNode } from "react";

export type EmptyStateProps = {
    readonly illustration?: ReactNode;
    readonly title?: string;
    readonly titleId?: string;
    readonly message?: string;
    readonly action?: ReactNode;
};

export function EmptyState({ illustration, title, titleId, message, action }: EmptyStateProps) {
    return (
        <div className="empty-state">
            {illustration !== undefined ? (
                <div className="empty-state__art">{illustration}</div>
            ) : null}
            {title !== undefined ? (
                <h2 id={titleId} className="empty-state__title">
                    {title}
                </h2>
            ) : null}
            {message !== undefined ? <p className="empty-state__text">{message}</p> : null}
            {action !== undefined ? <div className="empty-state__action">{action}</div> : null}
        </div>
    );
}
