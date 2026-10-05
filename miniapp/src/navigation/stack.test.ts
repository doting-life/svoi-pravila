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

        state = pushScreen(state, { name: "contactDetail", contactId: "c1" });
        expect(currentScreen(state).name).toBe("contactDetail");
        expect(canGoBack(state)).toBe(true);

        state = pushScreen(state, { name: "addRule", contactId: "c1" });
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
});
