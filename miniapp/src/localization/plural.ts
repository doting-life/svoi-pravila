import { ru } from "./ru";

function pickPlural(
    count: number,
    forms: { readonly one: string; readonly few: string; readonly many: string },
): string {
    const lastDigit = count % 10;
    const lastTwoDigits = count % 100;
    let template: string = forms.many;
    if (lastDigit === 1 && lastTwoDigits !== 11) {
        template = forms.one;
    } else if (lastDigit >= 2 && lastDigit <= 4 && (lastTwoDigits < 12 || lastTwoDigits > 14)) {
        template = forms.few;
    }
    return template.replace("{count}", String(count));
}

export function decodeRemainingLabel(count: number): string {
    return pickPlural(count, ru.decodeRemaining);
}

export function homeRulesCountLabel(count: number): string {
    return pickPlural(count, ru.homeRulesCount);
}
