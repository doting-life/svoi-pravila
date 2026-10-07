import { useState } from "react";

import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ScreenHeader } from "../components/ScreenHeader";
import { ErrorView, LoadingView } from "../components/StatusViews";
import { usePrivacyActions } from "../hooks/usePrivacyActions";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type DeleteConfirmScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly onCancelled: () => void;
    readonly onDeleted: () => void;
};

export function DeleteConfirmScreen({
    telegram,
    onCancelled,
    onDeleted,
}: DeleteConfirmScreenProps) {
    const texts = usePrivacyTexts();
    const actions = usePrivacyActions();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    if (texts.status === "loading") {
        return <LoadingView />;
    }
    if (texts.status === "error") {
        return <ErrorView message={ru.errorGeneric} onRetry={texts.refetch} />;
    }

    const confirmDelete = async () => {
        setBusy(true);
        setError(null);
        const result = await actions.deleteAccount();
        setBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            setError(result.error.message || ru.errorGeneric);
            return;
        }
        telegram.hapticNotification("success");
        onDeleted();
    };

    return (
        <section className="screen" aria-labelledby="delete-confirm-title">
            <ScreenHeader title={ru.privacyDeleteAction} titleId="delete-confirm-title" />
            <Card tone="danger">
                <p className="hint-text">{texts.data.delete.confirm}</p>
            </Card>
            {error !== null ? (
                <p className="form-error" role="alert">
                    {error}
                </p>
            ) : null}
            <div className="screen-footer">
                <Button
                    variant="danger"
                    size="lg"
                    block
                    disabled={busy}
                    onClick={() => {
                        void confirmDelete();
                    }}
                >
                    {busy ? ru.loading : ru.privacyDeleteForever}
                </Button>
                <Button variant="ghost" block disabled={busy} onClick={onCancelled}>
                    {ru.cancel}
                </Button>
            </div>
        </section>
    );
}
