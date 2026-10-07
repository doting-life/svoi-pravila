import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { MoonIllustration } from "../components/icons";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type DeletedScreenProps = {
    readonly telegram: TelegramAdapter;
};

export function DeletedScreen({ telegram }: DeletedScreenProps) {
    return (
        <section className="screen screen--centered" aria-labelledby="deleted-title">
            <EmptyState
                illustration={<MoonIllustration />}
                title={ru.privacyDeleted}
                titleId="deleted-title"
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
