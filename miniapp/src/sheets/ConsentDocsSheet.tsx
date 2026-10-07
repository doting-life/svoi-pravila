import { Button } from "../components/Button";
import { Sheet } from "../components/Sheet";
import { ErrorView, LoadingView } from "../components/StatusViews";
import { useConsentDocuments, type ConsentKind } from "../hooks/useConsentDocuments";
import { ru } from "../localization/ru";

function CloseButton({ onClose }: { readonly onClose: () => void }) {
    return (
        <Button variant="ghost" onClick={onClose}>
            {ru.consentDocsClose}
        </Button>
    );
}

export type ConsentTextSheetProps = {
    readonly title: string;
    readonly text: string;
    readonly onClose: () => void;
};

export function ConsentTextSheet({ title, text, onClose }: ConsentTextSheetProps) {
    return (
        <Sheet title={title} onClose={onClose} footer={<CloseButton onClose={onClose} />}>
            <p className="sheet__doc">{text}</p>
        </Sheet>
    );
}

export type ConsentDocsSheetProps = {
    readonly title: string;
    readonly kinds: readonly ConsentKind[];
    readonly onClose: () => void;
};

export function ConsentDocsSheet({ title, kinds, onClose }: ConsentDocsSheetProps) {
    const documents = useConsentDocuments(kinds);
    return (
        <Sheet title={title} onClose={onClose} footer={<CloseButton onClose={onClose} />}>
            {documents.status === "loading" ? <LoadingView /> : null}
            {documents.status === "error" ? (
                <ErrorView message={documents.error.message} onRetry={documents.refetch} />
            ) : null}
            {documents.status === "success"
                ? documents.data.map((document) => (
                      <section key={document.kind} className="stack stack--tight">
                          <h3 className="hint-title">{ru.consentKinds[document.kind]}</h3>
                          <p className="sheet__doc">{document.text}</p>
                      </section>
                  ))
                : null}
        </Sheet>
    );
}
