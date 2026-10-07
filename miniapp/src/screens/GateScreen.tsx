import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type GateScreenProps = {
    readonly kind: "unauthorized";
    readonly telegram: TelegramAdapter;
};

export function GateScreen({ telegram }: GateScreenProps) {
    return (
        <section className="gate" aria-labelledby="gate-title">
            <h2 id="gate-title" className="screen-title">
                {ru.gateUnauthorized}
            </h2>
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
