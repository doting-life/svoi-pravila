import { useCallback, useState } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapApiResult, unwrapEmptyResult } from "../api/request";
import type { components } from "../api/schema";
import { ErrorView, LoadingView } from "../components/StatusViews";
import { useAsyncResource } from "../hooks/useAsyncResource";
import { usePrivacyActions } from "../hooks/usePrivacyActions";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

type ConsentKind = components["schemas"]["ConsentDocumentResponse"]["kind"];
type Me = components["schemas"]["MeResponse"];

export type OnboardingConsentScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly me: Me;
    readonly onAdvanced: () => void;
    readonly onAccountDeleted?: () => void;
};

export function OnboardingConsentScreen({
    telegram,
    me,
    onAdvanced,
    onAccountDeleted,
}: OnboardingConsentScreenProps) {
    const client = useApiClient();
    const privacyTexts = usePrivacyTexts();
    const privacyActions = usePrivacyActions();
    const kind: ConsentKind = me.consent_kind ?? "personal_data";
    const documentResource = useAsyncResource(
        useCallback(async () => {
            const result = await client.GET("/api/v1/consents/{kind}/document", {
                params: { path: { kind } },
            });
            return unwrapApiResult(result);
        }, [client, kind]),
        `consent-doc:${kind}`,
    );
    const [busy, setBusy] = useState(false);
    const [declined, setDeclined] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [exportBusy, setExportBusy] = useState(false);
    const [exportMessage, setExportMessage] = useState<string | null>(null);
    const [deleteBusy, setDeleteBusy] = useState(false);

    const grant = async () => {
        if (documentResource.status !== "success") {
            return;
        }
        const document = documentResource.data;
        setBusy(true);
        setActionError(null);
        const result = await client.POST("/api/v1/me/consents", {
            body: { kind: document.kind, text_version: document.version },
        });
        const unwrapped = unwrapEmptyResult(result);
        setBusy(false);
        if (unwrapped.error !== undefined) {
            telegram.hapticNotification("error");
            setActionError(unwrapped.error.message || ru.errorGeneric);
            if (unwrapped.error.code === "consent_stale") {
                documentResource.refetch();
            }
            return;
        }
        telegram.hapticNotification("success");
        onAdvanced();
    };

    const exportData = async () => {
        setExportBusy(true);
        setExportMessage(null);
        setActionError(null);
        const result = await privacyActions.exportData(telegram);
        setExportBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            setActionError(result.error);
            return;
        }
        telegram.hapticNotification("success");
        setExportMessage(ru.privacyExportDone);
    };

    const deleteAccount = async () => {
        if (privacyTexts.status !== "success") {
            return;
        }
        const confirmed = await telegram.showConfirm(privacyTexts.data.delete.confirm);
        if (!confirmed) {
            return;
        }
        setDeleteBusy(true);
        const result = await privacyActions.deleteAccount();
        setDeleteBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            setActionError(result.error.message || ru.errorGeneric);
            return;
        }
        telegram.hapticNotification("success");
        onAccountDeleted?.();
    };

    if (declined) {
        return (
            <section className="screen" aria-labelledby="consent-title">
                <h2 id="consent-title" className="screen-title">
                    {ru.onboardingConsentTitle}
                </h2>
                <p className="status-message">{ru.onboardingConsentDeclined}</p>
                <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => {
                        telegram.close();
                    }}
                >
                    {ru.close}
                </button>
            </section>
        );
    }

    if (documentResource.status === "loading") {
        return <LoadingView />;
    }
    if (documentResource.status === "error") {
        return (
            <ErrorView
                message={documentResource.error.message || ru.errorGeneric}
                onRetry={documentResource.refetch}
            />
        );
    }

    const document = documentResource.data;
    const showStorage = kind === "personal_data";

    return (
        <section className="screen" aria-labelledby="consent-title">
            <h2 id="consent-title" className="screen-title">
                {ru.onboardingConsentTitle}
            </h2>
            {showStorage ? (
                <>
                    <p className="status-message" style={{ whiteSpace: "pre-line" }}>
                        {ru.onboardingStorageNotice}
                    </p>
                    <p className="status-message">{ru.onboardingViaBotNotice}</p>
                </>
            ) : null}
            <article className="block">
                <p className="status-message" style={{ whiteSpace: "pre-line" }}>
                    {document.text}
                </p>
            </article>
            <div className="list-item-actions">
                <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy}
                    onClick={() => {
                        void grant();
                    }}
                >
                    {busy ? ru.loading : ru.onboardingConsentAgree}
                </button>
                <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={() => {
                        setDeclined(true);
                    }}
                >
                    {ru.onboardingConsentDecline}
                </button>
            </div>
            {me.account_exists ? (
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
                        disabled={deleteBusy || privacyTexts.status !== "success"}
                        onClick={() => {
                            void deleteAccount();
                        }}
                    >
                        {deleteBusy ? ru.loading : ru.privacyDeleteAction}
                    </button>
                </div>
            ) : null}
            {exportMessage !== null ? (
                <p className="status-message" role="status">
                    {exportMessage}
                </p>
            ) : null}
            {actionError !== null ? (
                <p className="status-message" role="alert">
                    {actionError}
                </p>
            ) : null}
        </section>
    );
}
