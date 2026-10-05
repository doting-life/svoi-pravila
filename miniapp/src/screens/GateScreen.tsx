import { useState } from "react";

import { ErrorView, LoadingView } from "../components/StatusViews";
import { usePrivacyActions } from "../hooks/usePrivacyActions";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type GateScreenProps = {
    readonly kind: "incomplete" | "consent" | "unauthorized";
    readonly telegram: TelegramAdapter;
    readonly showRightsActions?: boolean;
    readonly onAccountDeleted?: () => void;
};

function GateRightsActions({
    telegram,
    onAccountDeleted,
}: {
    readonly telegram: TelegramAdapter;
    readonly onAccountDeleted?: () => void;
}) {
    const texts = usePrivacyTexts();
    const actions = usePrivacyActions();
    const [exportBusy, setExportBusy] = useState(false);
    const [exportMessage, setExportMessage] = useState<string | null>(null);
    const [exportError, setExportError] = useState<string | null>(null);
    const [deleteStep, setDeleteStep] = useState<"idle" | "confirm">("idle");
    const [deleteBusy, setDeleteBusy] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);

    if (texts.status === "loading") {
        return <LoadingView />;
    }
    if (texts.status === "error") {
        return <ErrorView message={ru.errorGeneric} onRetry={texts.refetch} />;
    }

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

    const beginDelete = async () => {
        const confirmed = await telegram.showConfirm(texts.data.delete.confirm);
        if (!confirmed) {
            return;
        }
        setDeleteError(null);
        setDeleteStep("confirm");
    };

    const confirmDelete = async () => {
        setDeleteBusy(true);
        setDeleteError(null);
        const result = await actions.deleteAccount();
        setDeleteBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            setDeleteError(result.error.message || ru.errorGeneric);
            return;
        }
        telegram.hapticNotification("success");
        onAccountDeleted?.();
    };

    return (
        <>
            {deleteStep === "idle" ? (
                <div className="list-item-actions">
                    <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={exportBusy}
                        onClick={() => {
                            void exportData();
                        }}
                    >
                        {exportBusy ? ru.loading : ru.privacyExportAction}
                    </button>
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
            ) : (
                <div className="list-item-actions">
                    <button
                        type="button"
                        className="btn btn-danger"
                        disabled={deleteBusy}
                        onClick={() => {
                            void confirmDelete();
                        }}
                    >
                        {deleteBusy ? ru.loading : ru.privacyDeleteForever}
                    </button>
                    <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={deleteBusy}
                        onClick={() => {
                            setDeleteStep("idle");
                            setDeleteError(null);
                        }}
                    >
                        {ru.cancel}
                    </button>
                </div>
            )}
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
            {deleteError !== null ? (
                <p className="status-message" role="alert">
                    {deleteError}
                </p>
            ) : null}
        </>
    );
}

export function GateScreen({
    kind,
    telegram,
    showRightsActions = false,
    onAccountDeleted,
}: GateScreenProps) {
    if (kind === "unauthorized") {
        return (
            <section className="gate" aria-labelledby="gate-title">
                <h2 id="gate-title" className="screen-title">
                    {ru.gateUnauthorized}
                </h2>
            </section>
        );
    }

    return (
        <section className="gate" aria-labelledby="gate-title">
            <h2 id="gate-title" className="screen-title">
                {ru.gateIncomplete}
            </h2>
            {kind === "consent" ? <p className="status-message">{ru.gateConsentExtra}</p> : null}
            <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                    telegram.close();
                }}
            >
                {ru.close}
            </button>
            {showRightsActions ? (
                onAccountDeleted !== undefined ? (
                    <GateRightsActions telegram={telegram} onAccountDeleted={onAccountDeleted} />
                ) : (
                    <GateRightsActions telegram={telegram} />
                )
            ) : null}
        </section>
    );
}
