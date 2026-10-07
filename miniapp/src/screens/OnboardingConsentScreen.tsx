import { useState } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapEmptyResult } from "../api/request";
import { Button } from "../components/Button";
import { CheckIcon, CrossIcon, InfoIcon } from "../components/icons";
import { ProgressSteps } from "../components/ProgressSteps";
import { ErrorView, LoadingView } from "../components/StatusViews";
import { useConsentDocuments, type ConsentKind } from "../hooks/useConsentDocuments";
import { usePrivacyActions } from "../hooks/usePrivacyActions";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { ru } from "../localization/ru";
import { ConsentTextSheet } from "../sheets/ConsentDocsSheet";
import type { TelegramAdapter } from "../telegram/webapp";

const ALL_KINDS: readonly ConsentKind[] = ["personal_data", "special_category"];

export type OnboardingConsentScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly consentKind: ConsentKind | null;
    readonly accountExists: boolean;
    readonly botUsername: string | null;
    readonly onAdvanced: () => void;
    readonly onAccountDeleted?: () => void;
};

export function OnboardingConsentScreen({
    telegram,
    consentKind,
    accountExists,
    botUsername,
    onAdvanced,
    onAccountDeleted,
}: OnboardingConsentScreenProps) {
    const client = useApiClient();
    const privacyTexts = usePrivacyTexts();
    const privacyActions = usePrivacyActions();
    const kinds = consentKind === "special_category" ? ["special_category" as const] : ALL_KINDS;
    const documents = useConsentDocuments(kinds);
    const [accepted, setAccepted] = useState<ReadonlySet<ConsentKind>>(new Set());
    const [granted, setGranted] = useState<ReadonlySet<ConsentKind>>(new Set());
    const [openDoc, setOpenDoc] = useState<ConsentKind | null>(null);
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [exportBusy, setExportBusy] = useState(false);
    const [exportMessage, setExportMessage] = useState<string | null>(null);
    const [deleteBusy, setDeleteBusy] = useState(false);

    const toggle = (kind: ConsentKind, checked: boolean) => {
        setAccepted((current) => {
            const next = new Set(current);
            if (checked) {
                next.add(kind);
            } else {
                next.delete(kind);
            }
            return next;
        });
    };

    const grantAll = async () => {
        if (documents.status !== "success") {
            return;
        }
        setBusy(true);
        setActionError(null);
        const done = new Set(granted);
        for (const document of documents.data) {
            if (done.has(document.kind)) {
                continue;
            }
            const result = await client.POST("/api/v1/me/consents", {
                body: { kind: document.kind, text_version: document.version },
            });
            const unwrapped = unwrapEmptyResult(result);
            if (unwrapped.error !== undefined) {
                setGranted(done);
                setBusy(false);
                telegram.hapticNotification("error");
                setActionError(unwrapped.error.message || ru.errorGeneric);
                if (unwrapped.error.code === "consent_stale") {
                    documents.refetch();
                }
                return;
            }
            done.add(document.kind);
        }
        setGranted(done);
        setBusy(false);
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

    if (documents.status === "loading") {
        return <LoadingView />;
    }
    if (documents.status === "error") {
        return (
            <ErrorView
                message={documents.error.message || ru.errorGeneric}
                onRetry={documents.refetch}
            />
        );
    }

    const allAccepted = kinds.every((kind) => accepted.has(kind));
    const openDocument = documents.data.find((document) => document.kind === openDoc);

    return (
        <section className="screen screen--onboarding" aria-labelledby="consent-title">
            <ProgressSteps current={2} total={2} />

            <header className="screen-header">
                <div className="screen-header__text">
                    <h1 id="consent-title" className="screen-title">
                        {ru.onboardingConsentTitle}
                    </h1>
                    <p className="screen-lead">{ru.onboardingConsentLead}</p>
                </div>
            </header>

            <div className="card">
                <ul className="check-list">
                    <li className="check-list__item">
                        <CheckIcon className="check-list__icon" size={20} />
                        <span>{ru.onboardingStoreEncrypted}</span>
                    </li>
                    <li className="check-list__item">
                        <CheckIcon className="check-list__icon" size={20} />
                        <span>{ru.onboardingStoreStats}</span>
                    </li>
                    <li className="check-list__item">
                        <CrossIcon className="check-list__icon check-list__icon--no" size={20} />
                        <span>{ru.onboardingStoreNever}</span>
                    </li>
                </ul>
            </div>

            <div className="section">
                {kinds.map((kind) => (
                    <div key={kind} className="check-card check-card--consent">
                        <div className="check-card__body">
                            <label className="check-card__title" htmlFor={`consent-${kind}`}>
                                {ru.consentKinds[kind]}
                            </label>
                            <button
                                type="button"
                                className="link-button"
                                onClick={() => {
                                    setOpenDoc(kind);
                                }}
                            >
                                {ru.consentDocLink}
                                <span className="visually-hidden"> — {ru.consentKinds[kind]}</span>
                            </button>
                        </div>
                        <input
                            id={`consent-${kind}`}
                            type="checkbox"
                            checked={accepted.has(kind)}
                            disabled={busy}
                            onChange={(event) => {
                                toggle(kind, event.target.checked);
                            }}
                        />
                    </div>
                ))}
            </div>

            {botUsername !== null ? (
                <div className="note note--warm">
                    <InfoIcon size={20} />
                    <span>{ru.onboardingViaBot.replace("{bot}", botUsername)}</span>
                </div>
            ) : null}

            {accountExists ? (
                <div className="row row--wrap">
                    <Button
                        variant="ghost"
                        disabled={exportBusy}
                        onClick={() => {
                            void exportData();
                        }}
                    >
                        {ru.privacyExportAction}
                    </Button>
                    <Button
                        variant="danger"
                        disabled={deleteBusy || privacyTexts.status !== "success"}
                        onClick={() => {
                            void deleteAccount();
                        }}
                    >
                        {ru.privacyDeleteAction}
                    </Button>
                </div>
            ) : null}
            {exportMessage !== null ? (
                <p className="form-status" role="status">
                    {exportMessage}
                </p>
            ) : null}
            {actionError !== null ? (
                <p className="form-error" role="alert">
                    {actionError}
                </p>
            ) : null}

            <div className="screen-footer">
                <Button
                    size="lg"
                    block
                    disabled={!allAccepted || busy}
                    onClick={() => {
                        void grantAll();
                    }}
                >
                    {ru.onboardingAccept}
                </Button>
            </div>

            {openDocument !== undefined ? (
                <ConsentTextSheet
                    title={ru.consentKinds[openDocument.kind]}
                    text={openDocument.text}
                    onClose={() => {
                        setOpenDoc(null);
                    }}
                />
            ) : null}
        </section>
    );
}
