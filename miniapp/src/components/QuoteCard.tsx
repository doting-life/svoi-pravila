import { Card } from "./Card";

export type QuoteCardProps = {
    readonly label: string;
    readonly text: string;
};

/** The user's own text shown above a result, so variants are read against it. */
export function QuoteCard({ label, text }: QuoteCardProps) {
    return (
        <Card className="quote" tone="sunken">
            <span className="quote__label">{label}</span>
            <p className="quote__text">{text}</p>
        </Card>
    );
}
