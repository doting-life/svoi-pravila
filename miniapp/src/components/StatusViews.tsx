import { ru } from "../localization/ru";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import { Skeleton } from "./Skeleton";

export function LoadingView() {
    return (
        <div className="loading-view" role="status">
            <span className="visually-hidden">{ru.loading}</span>
            <Skeleton shape="line" short />
            <Skeleton shape="block" />
            <Skeleton shape="block" />
        </div>
    );
}

export type ErrorViewProps = {
    readonly message?: string;
    readonly onRetry?: () => void;
};

export function ErrorView({ message, onRetry }: ErrorViewProps) {
    return (
        <div className="status-block" role="alert">
            <p className="status-block__text">{message ?? ru.errorGeneric}</p>
            {onRetry !== undefined ? (
                <Button variant="outline" onClick={onRetry}>
                    {ru.retry}
                </Button>
            ) : null}
        </div>
    );
}

export type EmptyViewProps = {
    readonly message: string;
};

export function EmptyView({ message }: EmptyViewProps) {
    return <EmptyState message={message} />;
}
