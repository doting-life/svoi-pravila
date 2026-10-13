import { Avatar } from "./Avatar";
import { ChevronDownIcon } from "./icons";
import { ru } from "../localization/ru";

export type ContactChipProps = {
    /** Label of the chosen contact, or `null` when no contact is chosen. */
    readonly label: string | null;
    readonly onClick: () => void;
};

/** «Для кого: …» chip that opens the contact picker. */
export function ContactChip({ label, onClick }: ContactChipProps) {
    return (
        <button type="button" className="contact-chip" onClick={onClick}>
            {label !== null ? (
                <Avatar name={label} size="sm" />
            ) : (
                <span className="avatar avatar--sm avatar--empty" aria-hidden="true">
                    –
                </span>
            )}
            <span className="contact-chip__label">
                {label !== null ? ru.contactChipFor.replace("{label}", label) : ru.contactChipNone}
            </span>
            <ChevronDownIcon size={18} />
        </button>
    );
}
