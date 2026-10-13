import { Button } from "../components/Button";
import { CrossIcon, LogoBubbles } from "../components/icons";
import { ru } from "../localization/ru";

export type OnboardingIntroScreenProps = {
    readonly botUsername: string;
    readonly onTryExample: () => void;
    readonly onDismiss: () => void;
};

/** One-screen intro shown once, before the first Home. */
export function OnboardingIntroScreen({
    botUsername,
    onTryExample,
    onDismiss,
}: OnboardingIntroScreenProps) {
    return (
        <section className="screen screen--onboarding" aria-labelledby="intro-title">
            <Button
                variant="ghost"
                className="intro-close"
                aria-label={ru.introClose}
                onClick={onDismiss}
            >
                <CrossIcon size={20} />
            </Button>

            <div className="onboarding-hero">
                <LogoBubbles />
                <h1 id="intro-title" className="onboarding-hero__title">
                    {ru.introTitle}
                </h1>
                <p className="onboarding-hero__tagline">{ru.introSubtitle}</p>
            </div>

            <ol className="intro-steps">
                {ru.introSteps.map((step, index) => (
                    <li key={step.title} className="intro-step">
                        <span className="intro-step__number" aria-hidden="true">
                            {index + 1}
                        </span>
                        <span>
                            <span className="intro-step__title">{step.title}</span>
                            <span className="intro-step__caption">{step.caption}</span>
                        </span>
                    </li>
                ))}
            </ol>

            <p className="hint-text">
                {ru.introInlineHintBefore}
                {botUsername}
                {ru.introInlineHintAfter}
            </p>

            <div className="screen-footer">
                <Button size="lg" block onClick={onTryExample}>
                    {ru.introTryExample}
                </Button>
                <Button variant="ghost" size="lg" block onClick={onDismiss}>
                    {ru.introSkip}
                </Button>
            </div>
        </section>
    );
}
