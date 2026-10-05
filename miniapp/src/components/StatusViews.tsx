import { ru } from "../localization/ru";

export function LoadingView() {
    return (
        <p className="status-message" role="status">
            {ru.loading}
        </p>
    );
}

export type ErrorViewProps = {
    readonly message?: string;
    readonly onRetry?: () => void;
};

export function ErrorView({ message, onRetry }: ErrorViewProps) {
    return (
        <div className="status-block" role="alert">
            <p className="status-message">{message ?? ru.errorGeneric}</p>
            {onRetry !== undefined ? (
                <button type="button" className="btn btn-secondary" onClick={onRetry}>
                    {ru.retry}
                </button>
            ) : null}
        </div>
    );
}

export type EmptyViewProps = {
    readonly message: string;
};

export function EmptyView({ message }: EmptyViewProps) {
    return <p className="status-message">{message}</p>;
}
