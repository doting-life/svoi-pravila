/** Fill the server-owned applied-rule citation template (C0). */

export function formatAppliedRuleCitation(
    template: string,
    parts: { readonly date: string; readonly text: string },
): string {
    return template.replaceAll("{date}", parts.date).replaceAll("{text}", parts.text);
}
