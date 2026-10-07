import { ru } from "./ru";

export function decodeRemainingLabel(count: number): string {
    const lastDigit = count % 10;
    const lastTwoDigits = count % 100;
    let template: string = ru.decodeRemaining.many;
    if (lastDigit === 1 && lastTwoDigits !== 11) {
        template = ru.decodeRemaining.one;
    } else if (lastDigit >= 2 && lastDigit <= 4 && (lastTwoDigits < 12 || lastTwoDigits > 14)) {
        template = ru.decodeRemaining.few;
    }
    return template.replace("{count}", String(count));
}
