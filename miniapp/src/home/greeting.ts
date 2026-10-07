import { ru } from "../localization/ru";

export function greetingFor(now: Date): string {
    const hour = now.getHours();
    if (hour >= 5 && hour < 12) {
        return ru.greetingMorning;
    }
    if (hour >= 12 && hour < 18) {
        return ru.greetingDay;
    }
    if (hour >= 18 && hour < 23) {
        return ru.greetingEvening;
    }
    return ru.greetingNight;
}
