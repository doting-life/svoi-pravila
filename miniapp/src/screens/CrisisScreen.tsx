import { Button } from "../components/Button";
import { HeartIcon, InfoIcon, PhoneIcon } from "../components/icons";
import { parseCrisisResource } from "../crisis/parseResource";
import { ru } from "../localization/ru";

export type CrisisScreenProps = {
    readonly lead: string | null;
    readonly resources: readonly string[];
    readonly onBack: () => void;
};

function ResourceCard({ line }: { readonly line: string }) {
    const resource = parseCrisisResource(line);
    const body = (
        <>
            <span className="resource-card__icon">
                <PhoneIcon size={20} />
            </span>
            <span className="resource-card__body">
                <span className="resource-card__title">{resource.title}</span>
                {resource.description !== null ? (
                    <span className="resource-card__text">{resource.description}</span>
                ) : null}
            </span>
        </>
    );
    if (resource.phone === null) {
        return <div className="resource-card">{body}</div>;
    }
    return (
        <a className="resource-card" href={`tel:${resource.phone}`}>
            {body}
        </a>
    );
}

export function CrisisScreen({ lead, resources, onBack }: CrisisScreenProps) {
    return (
        <section className="screen" aria-labelledby="crisis-title">
            <header className="screen-header">
                <div className="screen-header__text">
                    <span className="crisis-icon" aria-hidden="true">
                        <HeartIcon size={28} />
                    </span>
                    <h1 id="crisis-title" className="screen-title">
                        {ru.crisisTitle}
                    </h1>
                    <p className="crisis-lead">{lead ?? ru.crisisSupport}</p>
                </div>
            </header>

            <div className="stack">
                {resources.map((line) => (
                    <ResourceCard key={line} line={line} />
                ))}
                {resources.length === 0 ? (
                    <a className="resource-card" href="tel:112">
                        <span className="resource-card__icon resource-card__icon--urgent">
                            <PhoneIcon size={20} />
                        </span>
                        <span className="resource-card__body">
                            <span className="resource-card__title">{ru.crisisEmergencyTitle}</span>
                            <span className="resource-card__text">{ru.crisisEmergencyHint}</span>
                        </span>
                    </a>
                ) : null}
            </div>

            <div className="note note--quiet">
                <InfoIcon size={20} />
                <span>{ru.crisisAlways}</span>
            </div>

            <div className="screen-footer">
                <Button variant="outline" size="lg" block onClick={onBack}>
                    {ru.crisisBack}
                </Button>
            </div>
        </section>
    );
}
