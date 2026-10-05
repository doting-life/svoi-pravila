import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type GateScreenProps = {
    readonly kind: "incomplete" | "consent" | "unauthorized";
    readonly telegram: TelegramAdapter;
};

export function GateScreen({ kind, telegram }: GateScreenProps) {
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
        </section>
    );
}
