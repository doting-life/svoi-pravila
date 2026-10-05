export type Screen =
    | { readonly name: "contacts" }
    | {
          readonly name: "contactDetail";
          readonly contactId: string;
          readonly label: string;
          readonly relationship: string;
      }
    | { readonly name: "addRule"; readonly contactId: string }
    | { readonly name: "privacy" }
    | { readonly name: "deleteConfirm" };

export type NavigationState = {
    readonly stack: readonly Screen[];
};

export function createInitialNavigation(): NavigationState {
    return { stack: [{ name: "contacts" }] };
}

export function currentScreen(state: NavigationState): Screen {
    const top = state.stack[state.stack.length - 1];
    if (top === undefined) {
        return { name: "contacts" };
    }
    return top;
}

export function canGoBack(state: NavigationState): boolean {
    return state.stack.length > 1;
}

export function pushScreen(state: NavigationState, screen: Screen): NavigationState {
    return { stack: [...state.stack, screen] };
}

export function popScreen(state: NavigationState): NavigationState {
    if (state.stack.length <= 1) {
        return state;
    }
    return { stack: state.stack.slice(0, -1) };
}

export function resetToContacts(): NavigationState {
    return createInitialNavigation();
}
