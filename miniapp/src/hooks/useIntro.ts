import { useCallback, useEffect, useState } from "react";

import type { TelegramAdapter } from "../telegram/webapp";

export const INTRO_SEEN_KEY = "intro_seen";

export type IntroState = "loading" | "show" | "hidden";

/**
 * One-screen onboarding flag in Telegram CloudStorage. Read once on mount; the intro is
 * shown only when CloudStorage exists and the flag is not `"1"`. A failed read counts as seen.
 */
export function useIntro(telegram: TelegramAdapter): {
    readonly state: IntroState;
    readonly markSeen: () => void;
} {
    const storage = telegram.cloudStorage;
    const [state, setState] = useState<IntroState>(storage === null ? "hidden" : "loading");

    useEffect(() => {
        if (storage === null) {
            return undefined;
        }
        let cancelled = false;
        void storage.getItem(INTRO_SEEN_KEY).then(
            (value) => {
                if (!cancelled) {
                    setState(value === "1" ? "hidden" : "show");
                }
            },
            () => {
                if (!cancelled) {
                    setState("hidden");
                }
            },
        );
        return () => {
            cancelled = true;
        };
    }, [storage]);

    const markSeen = useCallback(() => {
        setState("hidden");
        if (storage !== null) {
            void storage.setItem(INTRO_SEEN_KEY, "1");
        }
    }, [storage]);

    return { state, markSeen };
}
