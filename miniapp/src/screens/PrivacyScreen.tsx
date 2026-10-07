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
        const result = await actions.exportData(telegram);
        setExportBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            setExportError(result.error);
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

            <p className="status-message">{catalog.export.description}</p>

            <ul className="list">
                <li className="list-item">
                    <div className="list-item-body">
                        <p className="list-item-title">{ru.privacyExportAction}</p>
                    </div>
                    <div className="list-item-actions">
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
                    </div>
                </li>
                <li className="list-item">
                    <div className="list-item-body">
                        <p className="list-item-title">{ru.privacyRevokeAction}</p>
                        <p className="status-message">{catalog.revoke.description}</p>
                    </div>
                    <div className="list-item-actions">
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
                    </div>
                </li>
                <li className="list-item">
                    <div className="list-item-body">
                        <p className="list-item-title">{ru.privacyDeleteAction}</p>
                        <p className="status-message">{catalog.delete.description}</p>
                    </div>
                    <div className="list-item-actions">
                        <button
                            type="button"
                            className="btn btn-danger"
                            onClick={() => {
                                void beginDelete();
                            }}
                        >
                            {ru.privacyDeleteAction}
                        </button>
                    </div>
                </li>
            </ul>

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
    );
}
