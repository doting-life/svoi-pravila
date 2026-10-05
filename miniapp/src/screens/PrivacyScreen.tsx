import { useState } from "react";

import { ErrorView, LoadingView } from "../components/StatusViews";
import { usePrivacyActions } from "../hooks/usePrivacyActions";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type PrivacyScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly onRevoked: () => void;
    readonly onDeleteRequested: () => void;
};

export function PrivacyScreen({ telegram, onRevoked, onDeleteRequested }: PrivacyScreenProps) {
    const texts = usePrivacyTexts();
    const actions = usePrivacyActions();
    const [exportBusy, setExportBusy] = useState(false);
    const [exportMessage, setExportMessage] = useState<string | null>(null);
    const [exportError, setExportError] = useState<string | null>(null);
    const [revokeBusy, setRevokeBusy] = useState(false);

    if (texts.status === "loading") {
        return <LoadingView />;
    }
    if (texts.status === "error") {
        return <ErrorView message={ru.errorGeneric} onRetry={texts.refetch} />;
    }

    const catalog = texts.data;

    const exportData = async () => {
        setExportBusy(true);
        setExportMessage(null);
        setExportError(null);
        const result = await actions.exportData();
        setExportBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            if (result.error.code === "bot_chat_unavailable") {
                setExportError(ru.privacyExportUnavailable);
                return;
            }
            if (result.error.code === "rate_limited" || result.error.status === 429) {
                setExportError(ru.rateLimited);
                return;
            }
            setExportError(result.error.message || ru.errorGeneric);
            return;
        }
        telegram.hapticNotification("success");
        setExportMessage(ru.privacyExportDone);
    };

    const revoke = async () => {
        const confirmed = await telegram.showConfirm(catalog.revoke.confirm);
        if (!confirmed) {
            return;
        }
        setRevokeBusy(true);
        const result = await actions.revokeConsents();
        setRevokeBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onRevoked();
    };

    const beginDelete = async () => {
        const confirmed = await telegram.showConfirm(catalog.delete.confirm);
        if (!confirmed) {
            return;
        }
        onDeleteRequested();
    };

    return (
        <section className="screen" aria-labelledby="privacy-title">
            <header className="screen-header">
                <h2 id="privacy-title" className="screen-title">
                    {ru.privacyTitle}
                </h2>
            </header>

            <section className="block" aria-labelledby="export-title">
                <h3 id="export-title" className="block-title">
                    {ru.privacyExportAction}
                </h3>
                <p className="status-message">{catalog.export.description}</p>
                <button
                    type="button"
                    className="btn btn-primary"
                    disabled={exportBusy}
                    onClick={() => {
                        void exportData();
                    }}
                >
                    {exportBusy ? ru.loading : ru.privacyExportAction}
                </button>
                {exportMessage !== null ? (
                    <p className="status-message" role="status">
                        {exportMessage}
                    </p>
                ) : null}
                {exportError !== null ? (
                    <p className="status-message" role="alert">
                        {exportError}
                    </p>
                ) : null}
            </section>

            <section className="block" aria-labelledby="revoke-title">
                <h3 id="revoke-title" className="block-title">
                    {ru.privacyRevokeAction}
                </h3>
                <p className="status-message">{catalog.revoke.description}</p>
                <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={revokeBusy}
                    onClick={() => {
                        void revoke();
                    }}
                >
                    {revokeBusy ? ru.loading : ru.privacyRevokeAction}
                </button>
            </section>

            <section className="block" aria-labelledby="delete-title">
                <h3 id="delete-title" className="block-title">
                    {ru.privacyDeleteAction}
                </h3>
                <p className="status-message">{catalog.delete.description}</p>
                <button
                    type="button"
                    className="btn btn-danger"
                    onClick={() => {
                        void beginDelete();
                    }}
                >
                    {ru.privacyDeleteAction}
                </button>
            </section>
        </section>
    );
}
