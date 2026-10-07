import type { ReactNode } from "react";

export type ScreenHeaderProps = {
    readonly title: string;
    readonly titleId?: string;
    readonly lead?: string;
    readonly eyebrow?: string;
    readonly trailing?: ReactNode;
};

export function ScreenHeader({ title, titleId, lead, eyebrow, trailing }: ScreenHeaderProps) {
    return (
        <header className="screen-header">
            <div className="screen-header__text">
                {eyebrow !== undefined ? <p className="screen-eyebrow">{eyebrow}</p> : null}
                <h1 id={titleId} className="screen-title">
                    {title}
                </h1>
                {lead !== undefined ? <p className="screen-lead">{lead}</p> : null}
            </div>
            {trailing}
        </header>
    );
}
