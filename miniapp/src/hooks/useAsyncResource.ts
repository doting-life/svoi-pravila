import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiError } from "../api/errors";

export type ResourceState<T> =
    | { readonly status: "loading" }
    | { readonly status: "success"; readonly data: T }
    | { readonly status: "error"; readonly error: ApiError };

type Resolved<T> = {
    readonly generation: string;
    readonly state: Exclude<ResourceState<T>, { status: "loading" }>;
};

export function useAsyncResource<T>(
    loader: () => Promise<{ data?: T; error?: ApiError }>,
    depsKey: string,
): ResourceState<T> & { refetch: () => void } {
    const [tick, setTick] = useState(0);
    const [resolved, setResolved] = useState<Resolved<T> | null>(null);
    const loaderRef = useRef(loader);
    const generation = `${depsKey}:${String(tick)}`;

    useEffect(() => {
        loaderRef.current = loader;
    });

    const refetch = useCallback(() => {
        setTick((value) => value + 1);
    }, []);

    useEffect(() => {
        let cancelled = false;
        void loaderRef.current().then((result) => {
            if (cancelled) {
                return;
            }
            if (result.error !== undefined) {
                setResolved({
                    generation,
                    state: { status: "error", error: result.error },
                });
                return;
            }
            if (result.data === undefined) {
                setResolved({
                    generation,
                    state: {
                        status: "error",
                        error: {
                            kind: "unknown",
                            status: undefined,
                            message: "empty response",
                            code: undefined,
                        },
                    },
                });
                return;
            }
            setResolved({
                generation,
                state: { status: "success", data: result.data },
            });
        });
        return () => {
            cancelled = true;
        };
    }, [generation]);

    const state: ResourceState<T> =
        resolved !== null && resolved.generation === generation
            ? resolved.state
            : { status: "loading" };

    return { ...state, refetch };
}
