import type { ReactNode } from "react";

export type IconProps = {
    readonly size?: number;
    readonly strokeWidth?: number;
    readonly className?: string;
};

function createIcon(paths: ReactNode, defaultStrokeWidth = 1.8) {
    return function Icon({ size = 22, strokeWidth = defaultStrokeWidth, className }: IconProps) {
        return (
            <svg
                className={className}
                width={size}
                height={size}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={strokeWidth}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
                focusable="false"
            >
                {paths}
            </svg>
        );
    };
}

export const HomeIcon = createIcon(
    <path d="M4 11l8-6 8 6v8a1 1 0 0 1-1 1h-4v-5H9v5H5a1 1 0 0 1-1-1z" />,
);

export const PeopleIcon = createIcon(
    <>
        <circle cx="9" cy="8" r="3.5" />
        <path d="M3 19c.8-3.4 3.2-5 6-5s5.2 1.6 6 5" />
        <path d="M16 5.5a3 3 0 0 1 0 5.5M18 14.5c1.6.7 2.6 2.2 3 4.5" />
    </>,
);

export const ShieldCheckIcon = createIcon(
    <>
        <path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" />
        <path d="M9.5 12l2 2 3.5-4" />
    </>,
);

export const ShieldIcon = createIcon(<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" />);

export const ChatIcon = createIcon(
    <>
        <path d="M4 5h16v10H9l-5 4z" />
        <path d="M9 9h6" />
        <path d="M9 12h3" />
    </>,
);

export const ChatOutlineIcon = createIcon(<path d="M4 5h16v10H9l-5 4z" />);

export const BookmarkIcon = createIcon(<path d="M6 4h12v16l-6-4-6 4z" />);

export const PencilIcon = createIcon(<path d="M4 20h4L19 9l-4-4L4 16z" />);

export const PlusIcon = createIcon(<path d="M12 5v14M5 12h14" />, 2);

export const ClipboardIcon = createIcon(
    <>
        <rect x="8" y="4" width="11" height="14" rx="2" />
        <path d="M5 8v10a2 2 0 0 0 2 2h9" />
    </>,
);

export const LockIcon = createIcon(
    <>
        <rect x="5" y="11" width="14" height="9" rx="2" />
        <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>,
);

export const ChevronDownIcon = createIcon(<path d="M6 9l6 6 6-6" />, 2);

export const ChevronRightIcon = createIcon(<path d="M9 6l6 6-6 6" />, 2);

export const DownloadIcon = createIcon(<path d="M12 4v11M7 10l5 5 5-5M5 20h14" />);

export const DocumentIcon = createIcon(
    <>
        <path d="M7 3h7l5 5v13H7z" />
        <path d="M14 3v5h5M10 13h6M10 17h4" />
    </>,
);

export const MinusCircleIcon = createIcon(
    <>
        <circle cx="12" cy="12" r="9" />
        <path d="M8 12h8" />
    </>,
);

export const CheckIcon = createIcon(<path d="M5 12l4 4 10-10" />, 2);

export const CrossIcon = createIcon(<path d="M6 6l12 12M18 6L6 18" />, 2);

export const InfoIcon = createIcon(
    <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8h.01M11 12h1v5h1" />
    </>,
);

export const KeyboardIcon = createIcon(
    <>
        <rect x="3" y="6" width="18" height="12" rx="3" />
        <path d="M7 10h.01M11 10h.01M15 10h.01M8 14h8" />
    </>,
);

export const HeartIcon = createIcon(
    <path d="M12 21s-7-4.5-9-9.5C1.6 7.6 4 4 7.5 4c2 0 3.5 1.1 4.5 2.6C13 5.1 14.5 4 16.5 4 20 4 22.4 7.6 21 11.5 19 16.5 12 21 12 21z" />,
);

export const PhoneIcon = createIcon(
    <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" />,
);

export const SmileIcon = createIcon(
    <>
        <circle cx="12" cy="12" r="9" />
        <path d="M8 13s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01" />
    </>,
);

export function LogoBubbles({ className }: { readonly className?: string }) {
    return (
        <svg
            className={className}
            width="96"
            height="80"
            viewBox="0 0 96 80"
            fill="none"
            aria-hidden="true"
            focusable="false"
        >
            <path
                className="art-accent"
                d="M8 14a10 10 0 0 1 10-10h34a10 10 0 0 1 10 10v20a10 10 0 0 1-10 10H30l-14 11V44h0a10 10 0 0 1-8-10z"
                strokeWidth="2"
            />
            <path
                className="art-warm"
                d="M88 36a10 10 0 0 0-10-10H48a10 10 0 0 0-10 10v18a10 10 0 0 0 10 10h18l13 10V64a10 10 0 0 0 9-10z"
                strokeWidth="2"
            />
            <path
                className="art-warm-line"
                d="M50 45h26M50 53h16"
                strokeWidth="2"
                strokeLinecap="round"
            />
        </svg>
    );
}

export function PairCircles({ className }: { readonly className?: string }) {
    return (
        <svg
            className={className}
            width="132"
            height="88"
            viewBox="0 0 132 88"
            fill="none"
            aria-hidden="true"
            focusable="false"
        >
            <circle className="art-accent" cx="48" cy="44" r="36" strokeWidth="2" />
            <circle className="art-warm" cx="84" cy="44" r="36" strokeWidth="2" />
            <path
                className="art-ink-line"
                d="M58 44h16M66 36v16"
                strokeWidth="2"
                strokeLinecap="round"
            />
        </svg>
    );
}

export function MoonIllustration({ className }: { readonly className?: string }) {
    return (
        <svg
            className={className}
            width="96"
            height="96"
            viewBox="0 0 96 96"
            fill="none"
            aria-hidden="true"
            focusable="false"
        >
            <circle className="art-sunken" cx="48" cy="48" r="44" />
            <path
                className="art-moon"
                d="M60 26a24 24 0 1 0 10 34 20 20 0 0 1-10-34z"
                strokeWidth="2.5"
                strokeLinejoin="round"
            />
            <path
                className="art-warm-line"
                d="M70 24v6M67 27h6M76 40v4M74 42h4"
                strokeWidth="2"
                strokeLinecap="round"
            />
        </svg>
    );
}
