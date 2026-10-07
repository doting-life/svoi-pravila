export type CrisisResource = {
    readonly title: string;
    readonly description: string | null;
    readonly phone: string | null;
};

const SEPARATOR = " — ";

/** Split a catalog line like "112 — единый номер…" into a title, description and dialable number. */
export function parseCrisisResource(line: string): CrisisResource {
    const separatorAt = line.indexOf(SEPARATOR);
    if (separatorAt === -1) {
        return { title: line, description: null, phone: null };
    }
    const title = line.slice(0, separatorAt);
    const description = line.slice(separatorAt + SEPARATOR.length);
    const dialable = title.replace(/[^\d+]/g, "");
    return {
        title,
        description,
        phone: /\d/.test(dialable) ? dialable : null,
    };
}
