import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { InfoIcon, MoonIllustration } from "../components/icons";
import type { LimitKind } from "../navigation/stack";
import { ru } from "../localization/ru";

export type LimitScreenProps = {
    readonly kind: LimitKind;
    readonly message: string | null;
    readonly onOpenCrisis: () => void;
    readonly onClose: () => void;
};

export function LimitScreen({ kind, message, onOpenCrisis, onClose }: LimitScreenProps) {
    return (
        <section className="screen screen--centered" aria-labelledby="limit-title">
            <EmptyState
                illustration={<MoonIllustration />}
                title={kind === "quota" ? ru.limitTitleQuota : ru.limitTitleBudget}
                titleId="limit-title"
                {...(message !== null ? { message } : {})}
            />
            <div className="note note--quiet">
                <InfoIcon size={20} />
                <span>{ru.limitNote}</span>
            </div>
            <p className="hint-text">
                {ru.limitHelpBefore}
                <button type="button" className="link-button" onClick={onOpenCrisis}>
                    {ru.limitHelpLink}
                </button>
                {ru.limitHelpAfter}
            </p>
            <div className="screen-footer">
                <Button size="lg" block onClick={onClose}>
                    {ru.limitOk}
                </Button>
            </div>
        </section>
    );
}
