import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { LogoBubbles } from "../components/icons";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type GateScreenProps = {
    readonly kind: "unauthorized";
    readonly telegram: TelegramAdapter;
};

export function GateScreen({ telegram }: GateScreenProps) {
    return (
        <section className="screen screen--centered" aria-labelledby="gate-title">
            <EmptyState
                illustration={<LogoBubbles />}
                title={ru.gateUnauthorized}
                titleId="gate-title"
                action={
                    <Button
                        onClick={() => {
                            telegram.close();
                        }}
                    >
                        {ru.close}
                    </Button>
                }
            />
        </section>
    );
}
