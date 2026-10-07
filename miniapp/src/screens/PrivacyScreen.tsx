import { useState } from "react";

import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { DocumentIcon, DownloadIcon, MinusCircleIcon } from "../components/icons";
import { ListGroup, ListRow } from "../components/ListRow";
import { ScreenHeader } from "../components/ScreenHeader";
import { ErrorView, LoadingView } from "../components/StatusViews";
import { usePrivacyActions } from "../hooks/usePrivacyActions";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { ru } from "../localization/ru";
import { ConsentDocsSheet } from "../sheets/ConsentDocsSheet";
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
    const [docsOpen, setDocsOpen] = useState(false);

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
            <ScreenHeader title={ru.privacyTitle} titleId="privacy-title" lead={ru.privacyLead} />

            <ListGroup>
                <ListRow
                    icon={<DownloadIcon size={20} />}
                    title={ru.privacyExportAction}
                    subtitle={catalog.export.description}
                    disabled={exportBusy}
                    onClick={() => {
                        void exportData();
                    }}
                />
                <ListRow
                    icon={<DocumentIcon size={20} />}
                    title={ru.privacyConsentsAction}
                    subtitle={ru.privacyConsentsHint}
                    onClick={() => {
                        setDocsOpen(true);
                    }}
                />
                <ListRow
                    icon={<MinusCircleIcon size={20} />}
                    title={ru.privacyRevokeAction}
                    subtitle={catalog.revoke.description}
                    disabled={revokeBusy}
                    onClick={() => {
                        void revoke();
                    }}
                />
            </ListGroup>

            {exportMessage !== null ? (
                <p className="form-status" role="status">
                    {exportMessage}
                </p>
            ) : null}
            {exportError !== null ? (
                <p className="form-error" role="alert">
                    {exportError}
                </p>
            ) : null}

            <Card tone="danger" aria-labelledby="privacy-delete-title">
                <h2 id="privacy-delete-title" className="hint-title">
                    {ru.privacyDeleteAction}
                </h2>
                <p className="hint-text">{catalog.delete.description}</p>
                <Button
                    variant="danger"
                    onClick={() => {
                        void beginDelete();
                    }}
                >
                    {ru.privacyDeleteButton}
                </Button>
            </Card>

            {docsOpen ? (
                <ConsentDocsSheet
                    title={ru.privacyConsentsAction}
                    kinds={["personal_data", "special_category"]}
                    onClose={() => {
                        setDocsOpen(false);
                    }}
                />
            ) : null}
        </section>
    );
}
