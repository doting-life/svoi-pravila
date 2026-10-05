import { useState } from "react";

import { usePrivacyActions } from "../hooks/usePrivacyActions";
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
    const actions = usePrivacyActions();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

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
            <header className="screen-header">
                <h2 id="delete-confirm-title" className="screen-title">
                    {ru.privacyDeleteAction}
                </h2>
            </header>
            <p className="status-message">{ru.privacyDeleteConfirm}</p>
            <div className="list-item-actions">
                <button
                    type="button"
                    className="btn btn-danger"
                    disabled={busy}
                    onClick={() => {
                        void confirmDelete();
                    }}
                >
                    {busy ? ru.loading : ru.privacyDeleteForever}
                </button>
                <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={onCancelled}
                >
                    {ru.cancel}
                </button>
            </div>
            {error !== null ? (
                <p className="status-message" role="alert">
                    {error}
                </p>
            ) : null}
        </section>
    );
}
