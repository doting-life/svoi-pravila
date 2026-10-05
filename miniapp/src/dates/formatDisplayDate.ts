/**
 * Format a calendar date like the bot: day + long Russian month, year only when
 * it differs from `now` in the given IANA zone.
 */
export function formatDisplayDate(iso: string, timeZone: string, now: Date = new Date()): string {
    const when = new Date(iso);
    const dayMonth = new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "long",
        timeZone,
    }).format(when);

    const yearFormatter = new Intl.DateTimeFormat("en-US", {
        year: "numeric",
        timeZone,
    });
    const whenYear = yearFormatter.format(when);
    const nowYear = yearFormatter.format(now);
    if (whenYear === nowYear) {
        return dayMonth;
    }
    return `${dayMonth} ${whenYear}`;
}
