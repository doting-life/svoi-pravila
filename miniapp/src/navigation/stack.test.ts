import { describe, expect, it } from "vitest";

import {
    canGoBack,
    createInitialNavigation,
    currentScreen,
    popScreen,
    pushScreen,
    resetToContacts,
} from "./stack";

describe("navigation stack", () => {
    it("starts on contacts and supports push/pop", () => {
        let state = createInitialNavigation();
        expect(currentScreen(state)).toEqual({ name: "contacts" });
        expect(canGoBack(state)).toBe(false);

        state = pushScreen(state, {
            name: "contactDetail",
            contactId: "c1",
            label: "Аня",
            relationship: "partner",
            paired: false,
        });
        expect(currentScreen(state).name).toBe("contactDetail");
        expect(canGoBack(state)).toBe(true);

        state = pushScreen(state, { name: "addRule", contactId: "c1", paired: false });
        expect(currentScreen(state).name).toBe("addRule");

        state = popScreen(state);
        expect(currentScreen(state).name).toBe("contactDetail");
        state = popScreen(state);
        expect(currentScreen(state).name).toBe("contacts");
        state = popScreen(state);
        expect(currentScreen(state).name).toBe("contacts");
    });

    it("falls back to contacts for an empty stack and reset", () => {
        expect(currentScreen({ stack: [] })).toEqual({ name: "contacts" });
        expect(resetToContacts()).toEqual(createInitialNavigation());
    });

    it("supports privacy, decode and delete-confirm screens", () => {
        let state = createInitialNavigation();
        state = pushScreen(state, { name: "privacy" });
        expect(currentScreen(state).name).toBe("privacy");
        state = pushScreen(state, { name: "decode" });
        expect(currentScreen(state)).toEqual({ name: "decode" });
        state = pushScreen(state, { name: "deleteConfirm" });
        expect(currentScreen(state).name).toBe("deleteConfirm");
        state = resetToContacts();
        expect(currentScreen(state).name).toBe("contacts");
    });
});
