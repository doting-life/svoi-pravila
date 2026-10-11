/** Build a Telegram deep link that opens a chat with prefilled text (ADR-0013). */
export function partnerSendUrl(username: string, text: string): string {
    const bare = username.startsWith("@") ? username.slice(1) : username;
    return `https://t.me/${bare}?text=${encodeURIComponent(text)}`;
}
