import { createContext, useContext, useMemo, type ReactNode } from "react";

import { createApiClient, type ApiClient } from "./client";

const ApiContext = createContext<ApiClient | null>(null);

export type ApiProviderProps = {
    readonly initData: string;
    readonly fetchImpl?: typeof fetch;
    readonly client?: ApiClient;
    readonly children: ReactNode;
};

export function ApiProvider({ initData, fetchImpl, client, children }: ApiProviderProps) {
    const value = useMemo(() => {
        if (client !== undefined) {
            return client;
        }
        if (fetchImpl !== undefined) {
            return createApiClient({ initData, fetch: fetchImpl });
        }
        return createApiClient({ initData });
    }, [client, fetchImpl, initData]);
    return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApiClient(): ApiClient {
    const client = useContext(ApiContext);
    if (client === null) {
        throw new Error("useApiClient must be used within ApiProvider");
    }
    return client;
}
