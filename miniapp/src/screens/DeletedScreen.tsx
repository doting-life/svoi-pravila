import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type DeletedScreenProps = {
    readonly telegram: TelegramAdapter;
};

export function DeletedScreen({ telegram }: DeletedScreenProps) {
    return (
        <section className="screen" aria-labelledby="deleted-title">
            <header className="screen-header">
                <h2 id="deleted-title" className="screen-title">
                    {ru.privacyDeleted}
                </h2>
            </header>
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
