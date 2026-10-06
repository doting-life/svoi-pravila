import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult, unwrapEmptyResult } from "../api/request";
import type { components } from "../api/schema";
import { useAsyncResource, type ResourceState } from "./useAsyncResource";

export type Contact = components["schemas"]["ContactItem"];
export type Relationship = components["schemas"]["CreateContactRequest"]["relationship"];
export type InviteResult = components["schemas"]["InviteResponse"];

export function useContacts(): ResourceState<readonly Contact[]> & {
    refetch: () => void;
    createContact: (input: {
        label: string;
        relationship: Relationship;
    }) => Promise<{ data?: Contact; error?: ApiError }>;
    renameContact: (
        contactId: string,
        label: string,
    ) => Promise<{ data?: Contact; error?: ApiError }>;
    activateContact: (contactId: string) => Promise<{ error?: ApiError }>;
    inviteContact: (contactId: string) => Promise<{ data?: InviteResult; error?: ApiError }>;
    leavePair: (contactId: string) => Promise<{ error?: ApiError }>;
} {
    const client = useApiClient();
    const loader = useCallback(async () => {
        const result = await client.GET("/api/v1/contacts");
        const unwrapped = unwrapApiResult(result);
        if (unwrapped.error !== undefined) {
            return { error: unwrapped.error };
        }
        return { data: unwrapped.data?.contacts ?? [] };
    }, [client]);
    const { refetch, ...resource } = useAsyncResource(loader, "contacts");

    const createContact = useCallback(
        async (input: { label: string; relationship: Relationship }) => {
            const result = await client.POST("/api/v1/contacts", { body: input });
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    const renameContact = useCallback(
        async (contactId: string, label: string) => {
            const result = await client.PATCH("/api/v1/contacts/{contact_id}", {
                params: { path: { contact_id: contactId } },
                body: { label },
            });
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    const activateContact = useCallback(
        async (contactId: string) => {
            const result = await client.POST("/api/v1/contacts/{contact_id}/activate", {
                params: { path: { contact_id: contactId } },
            });
            const unwrapped = unwrapEmptyResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    const inviteContact = useCallback(
        async (contactId: string) => {
            const result = await client.POST("/api/v1/contacts/{contact_id}/invite", {
                params: { path: { contact_id: contactId } },
            });
            return unwrapApiResult(result);
        },
        [client],
    );

    const leavePair = useCallback(
        async (contactId: string) => {
            const result = await client.POST("/api/v1/contacts/{contact_id}/leave", {
                params: { path: { contact_id: contactId } },
                body: { confirm: true },
            });
            const unwrapped = unwrapEmptyResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    return {
        ...resource,
        refetch,
        createContact,
        renameContact,
        activateContact,
        inviteContact,
        leavePair,
    };
}
