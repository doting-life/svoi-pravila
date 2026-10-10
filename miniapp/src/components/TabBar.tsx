import type { ComponentType } from "react";

import { ru } from "../localization/ru";
import type { RootTab } from "../navigation/stack";
import { hapticSelection } from "../telegram/webapp";
import { HomeIcon, PeopleIcon, ShieldCheckIcon, type IconProps } from "./icons";

const TABS: readonly {
    readonly id: RootTab;
    readonly label: string;
    readonly Icon: ComponentType<IconProps>;
}[] = [
    { id: "home", label: ru.tabHome, Icon: HomeIcon },
    { id: "people", label: ru.tabPeople, Icon: PeopleIcon },
    { id: "privacy", label: ru.tabPrivacy, Icon: ShieldCheckIcon },
];

export type TabBarProps = {
    readonly active: RootTab;
    readonly onChange: (tab: RootTab) => void;
    readonly badges?: Partial<Record<RootTab, number>>;
};

export function TabBar({ active, onChange, badges }: TabBarProps) {
    return (
        <nav className="tab-bar" aria-label={ru.tabsLabel}>
            {TABS.map(({ id, label, Icon }) => {
                const count = badges?.[id] ?? 0;
                return (
                    <button
                        key={id}
                        type="button"
                        className={id === active ? "tab-bar__item is-active" : "tab-bar__item"}
                        aria-current={id === active ? "page" : undefined}
                        aria-label={label}
                        title={count > 0 ? `${label}: ${String(count)}` : label}
                        onClick={() => {
                            if (id !== active) {
                                hapticSelection();
                            }
                            onChange(id);
                        }}
                    >
                        <Icon />
                        <span aria-hidden="true">{label}</span>
                        {count > 0 ? (
                            <span className="tab-bar__badge" aria-hidden="true">
                                {count > 99 ? "99+" : count}
                            </span>
                        ) : null}
                    </button>
                );
            })}
        </nav>
    );
}
