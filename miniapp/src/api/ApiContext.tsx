import { createContext, useContext, useMemo, type ReactNode } from "react";

import { createApiClient, type ApiClient, type ApiCredential } from "./client";

const ApiContext = createContext<ApiClient | null>(null);
const CredentialContext = createContext<ApiCredential | null>(null);

export type ApiProviderProps = {
    readonly credential: ApiCredential;
    readonly fetchImpl?: typeof fetch;
    readonly client?: ApiClient;
    readonly children: ReactNode;
};

export function ApiProvider({ credential, fetchImpl, client, children }: ApiProviderProps) {
    const value = useMemo(() => {
        if (client !== undefined) {
            return client;
        }
        if (fetchImpl !== undefined) {
            return createApiClient({ credential, fetch: fetchImpl });
        }
        return createApiClient({ credential });
    }, [client, credential, fetchImpl]);
    return (
        <CredentialContext.Provider value={credential}>
            <ApiContext.Provider value={value}>{children}</ApiContext.Provider>
        </CredentialContext.Provider>
    );
}

export function useApiClient(): ApiClient {
    const client = useContext(ApiContext);
    if (client === null) {
        throw new Error("useApiClient must be used within ApiProvider");
    }
    return client;
}

export function useApiCredential(): ApiCredential {
    const credential = useContext(CredentialContext);
    if (credential === null) {
        throw new Error("useApiCredential must be used within ApiProvider");
    }
    return credential;
}
