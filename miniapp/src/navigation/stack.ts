export type RootTab = "home" | "people" | "privacy";

export const ROOT_TABS: readonly RootTab[] = ["home", "people", "privacy"];

export type LimitKind = "quota" | "budget";

export type OverlayScreen =
    | {
          readonly name: "contactDetail";
          readonly contactId: string;
          readonly label: string;
          readonly relationship: string;
          readonly paired: boolean;
      }
    | {
          readonly name: "addRule";
          readonly contactId: string;
          readonly label: string;
          readonly paired: boolean;
          readonly initialCategory?: string;
          readonly initialText?: string;
      }
    | { readonly name: "decode" }
    | { readonly name: "limit"; readonly kind: LimitKind; readonly message: string | null }
    | { readonly name: "deleteConfirm" }
    | { readonly name: "invite" }
    | {
          readonly name: "crisis";
          readonly lead: string | null;
          readonly resources: readonly string[];
      };

export type Screen = { readonly name: RootTab } | OverlayScreen;

export type NavigationState = {
    readonly root: RootTab;
    readonly stack: readonly OverlayScreen[];
};

export function createInitialNavigation(stack: readonly OverlayScreen[] = []): NavigationState {
    return { root: "home", stack };
}

export function currentScreen(state: NavigationState): Screen {
    const top = state.stack[state.stack.length - 1];
    if (top === undefined) {
        return { name: state.root };
    }
    return top;
}

export function canGoBack(state: NavigationState): boolean {
    return state.stack.length > 0;
}

export function pushScreen(state: NavigationState, screen: OverlayScreen): NavigationState {
    return { root: state.root, stack: [...state.stack, screen] };
}

export function popScreen(state: NavigationState): NavigationState {
    if (state.stack.length === 0) {
        return state;
    }
    return { root: state.root, stack: state.stack.slice(0, -1) };
}

export function selectRoot(root: RootTab): NavigationState {
    return { root, stack: [] };
}

export function replaceTop(state: NavigationState, screen: OverlayScreen): NavigationState {
    if (state.stack.length === 0) {
        return state;
    }
    return { root: state.root, stack: [...state.stack.slice(0, -1), screen] };
}
