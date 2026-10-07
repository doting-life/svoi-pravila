import { useEffect, useId, useRef, type ReactNode } from "react";

export type SheetProps = {
    readonly title: string;
    readonly onClose: () => void;
    readonly children: ReactNode;
    readonly footer?: ReactNode;
};

export function Sheet({ title, onClose, children, footer }: SheetProps) {
    const titleId = useId();
    const panelRef = useRef<HTMLDivElement>(null);
    const closeRef = useRef(onClose);

    useEffect(() => {
        closeRef.current = onClose;
    });

    useEffect(() => {
        panelRef.current?.focus();
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                closeRef.current();
            }
        };
        document.addEventListener("keydown", onKeyDown);
        return () => {
            document.removeEventListener("keydown", onKeyDown);
        };
    }, []);

    return (
        <div
            className="sheet-backdrop"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget) {
                    onClose();
                }
            }}
        >
            <div
                ref={panelRef}
                className="sheet"
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                tabIndex={-1}
            >
                <h2 id={titleId} className="sheet__title">
                    {title}
                </h2>
                <div className="sheet__body">{children}</div>
                {footer !== undefined ? <div className="sheet__footer">{footer}</div> : null}
            </div>
        </div>
    );
}
