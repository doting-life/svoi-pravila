import { describe, expect, it } from "vitest";

import {
    canGoBack,
    createInitialNavigation,
    currentScreen,
    popScreen,
    pushScreen,
    replaceTop,
    selectRoot,
} from "./stack";

describe("navigation stack", () => {
    it("starts on home and supports push/pop of overlays", () => {
        let state = createInitialNavigation();
        expect(currentScreen(state)).toEqual({ name: "home" });
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

        state = pushScreen(state, {
            name: "addRule",
            contactId: "c1",
            label: "Аня",
            paired: false,
        });
        expect(currentScreen(state).name).toBe("addRule");

        state = popScreen(state);
        expect(currentScreen(state).name).toBe("contactDetail");
        state = popScreen(state);
        expect(currentScreen(state)).toEqual({ name: "home" });
        expect(popScreen(state)).toBe(state);
    });

    it("can start with an overlay already on the stack", () => {
        const state = createInitialNavigation([{ name: "invite" }]);
        expect(currentScreen(state)).toEqual({ name: "invite" });
        expect(canGoBack(state)).toBe(true);
        expect(currentScreen(popScreen(state))).toEqual({ name: "home" });
    });

    it("switches root tabs and clears the overlay stack", () => {
        const opened = pushScreen(createInitialNavigation(), { name: "decode" });
        expect(canGoBack(opened)).toBe(true);
        const people = selectRoot("people");
        expect(currentScreen(people)).toEqual({ name: "people" });
        expect(canGoBack(people)).toBe(false);

        const confirm = pushScreen(selectRoot("privacy"), { name: "deleteConfirm" });
        expect(currentScreen(confirm).name).toBe("deleteConfirm");
        expect(popScreen(confirm).root).toBe("privacy");
    });

    it("replaces the top overlay in place", () => {
        let state = pushScreen(createInitialNavigation(), { name: "decode" });
        state = replaceTop(state, { name: "limit", kind: "quota", message: null });
        expect(state.stack).toEqual([{ name: "limit", kind: "quota", message: null }]);
        state = replaceTop(state, { name: "crisis", lead: null, resources: [] });
        expect(currentScreen(state).name).toBe("crisis");
    });

    it("ignores replaceTop on an empty stack", () => {
        const state = createInitialNavigation();
        expect(replaceTop(state, { name: "decode" })).toBe(state);
    });
});
