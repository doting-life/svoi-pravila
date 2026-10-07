import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ru } from "../localization/ru";
import { Avatar } from "./Avatar";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { Card } from "./Card";
import { ChipGroup } from "./ChipGroup";
import { EmptyState } from "./EmptyState";
import { TextArea, TextInput } from "./Field";
import { ListGroup, ListRow } from "./ListRow";
import { ProgressSteps } from "./ProgressSteps";
import { SegmentedControl } from "./SegmentedControl";
import { Sheet } from "./Sheet";
import { Skeleton } from "./Skeleton";
import { TabBar } from "./TabBar";

function installHaptics() {
    const impactOccurred = vi.fn();
    const selectionChanged = vi.fn();
    Object.defineProperty(window, "Telegram", {
        configurable: true,
        value: { WebApp: { HapticFeedback: { impactOccurred, selectionChanged } } },
    });
    return { impactOccurred, selectionChanged };
}

describe("Button", () => {
    it("fires a light impact and the click handler", () => {
        const { impactOccurred } = installHaptics();
        const onClick = vi.fn();
        render(<Button onClick={onClick}>Нажать</Button>);
        fireEvent.click(screen.getByRole("button", { name: "Нажать" }));
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(impactOccurred).toHaveBeenCalledWith("light");
    });

    it("applies variant, tone and size classes", () => {
        render(
            <Button variant="ghost" tone="danger" size="lg" block bare className="extra">
                Удалить
            </Button>,
        );
        const button = screen.getByRole("button", { name: "Удалить" });
        expect(button).toHaveClass(
            "btn",
            "btn--ghost",
            "btn--tone-danger",
            "btn--lg",
            "btn--block",
            "btn--bare",
            "extra",
        );
        expect(button).toHaveAttribute("type", "button");
    });

    it("works without a click handler", () => {
        render(<Button variant="outline">Пусто</Button>);
        fireEvent.click(screen.getByRole("button", { name: "Пусто" }));
        expect(screen.getByRole("button", { name: "Пусто" })).toHaveClass("btn--outline");
    });
});

describe("Card, Badge, Avatar, Skeleton", () => {
    it("renders tone classes and the first letter of a name", () => {
        render(
            <>
                <Card tone="warm" as="article" aria-labelledby="t">
                    <h2 id="t">Заголовок</h2>
                </Card>
                <Badge tone="accent">Метка</Badge>
                <Badge>Нейтральная</Badge>
                <Avatar name="  аня" />
                <Avatar name="" size="lg" />
                <Skeleton shape="circle" short className="x" />
            </>,
        );
        expect(screen.getByRole("article", { name: "Заголовок" })).toHaveClass("card--warm");
        expect(screen.getByText("Метка")).toHaveClass("badge--accent");
        expect(screen.getByText("Нейтральная")).toHaveClass("badge--neutral");
        expect(screen.getByText("А")).toHaveClass("avatar--md");
    });
});

describe("TabBar", () => {
    it("marks the active tab and reports changes with selection haptics", () => {
        const { selectionChanged } = installHaptics();
        const onChange = vi.fn();
        render(<TabBar active="home" onChange={onChange} />);
        expect(screen.getByRole("button", { name: ru.tabHome })).toHaveAttribute(
            "aria-current",
            "page",
        );
        fireEvent.click(screen.getByRole("button", { name: ru.tabHome }));
        expect(selectionChanged).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: ru.tabPeople }));
        expect(selectionChanged).toHaveBeenCalledTimes(1);
        expect(onChange).toHaveBeenLastCalledWith("people");
        expect(screen.getByRole("navigation", { name: ru.tabsLabel })).toBeInTheDocument();
    });
});

describe("ChipGroup and SegmentedControl", () => {
    function Harness() {
        const [chip, setChip] = useState<"a" | "b">("a");
        const [segment, setSegment] = useState<"x" | "y">("x");
        return (
            <>
                <ChipGroup
                    legend="Чипы"
                    options={[
                        { value: "a", label: "Первый" },
                        { value: "b", label: "Второй" },
                    ]}
                    value={chip}
                    onChange={setChip}
                />
                <SegmentedControl
                    legend="Сегменты"
                    options={[
                        { value: "x", label: "Икс" },
                        { value: "y", label: "Игрек" },
                    ]}
                    value={segment}
                    onChange={setSegment}
                />
            </>
        );
    }

    it("selects exactly one option per group with selection haptics", () => {
        const { selectionChanged } = installHaptics();
        render(<Harness />);
        expect(screen.getByLabelText("Первый")).toBeChecked();
        fireEvent.click(screen.getByLabelText("Второй"));
        expect(screen.getByLabelText("Второй")).toBeChecked();
        expect(screen.getByLabelText("Первый")).not.toBeChecked();
        fireEvent.click(screen.getByLabelText("Игрек"));
        expect(screen.getByLabelText("Игрек")).toBeChecked();
        expect(selectionChanged).toHaveBeenCalledTimes(2);
        expect(screen.getByRole("group", { name: "Чипы" })).toBeInTheDocument();
        expect(screen.getByRole("group", { name: "Сегменты" })).toBeInTheDocument();
    });
});

describe("Field", () => {
    it("shows a live counter and forwards edits", () => {
        const onChange = vi.fn();
        render(
            <>
                <TextArea
                    label="Текст"
                    value="abc"
                    maxLength={10}
                    footer="подсказка"
                    large
                    onChange={onChange}
                />
                <TextInput
                    label="Имя"
                    value=""
                    maxLength={5}
                    disabled
                    placeholder="имя"
                    onChange={onChange}
                />
            </>,
        );
        expect(screen.getByText("3 / 10")).toBeInTheDocument();
        expect(screen.getByText("подсказка")).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText("Текст"), { target: { value: "abcd" } });
        expect(onChange).toHaveBeenCalledWith("abcd");
        expect(screen.getByLabelText("Имя")).toBeDisabled();
    });

    it("edits a single-line input", () => {
        const onChange = vi.fn();
        render(<TextInput label="Имя" value="" maxLength={5} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText("Имя"), { target: { value: "Аня" } });
        expect(onChange).toHaveBeenCalledWith("Аня");
    });
});

describe("ListRow", () => {
    it("names the row by its title and describes it by the subtitle", () => {
        const onClick = vi.fn();
        render(
            <ListGroup>
                <ListRow
                    icon={<span>i</span>}
                    title="Скачать"
                    subtitle="Файл с данными"
                    trailing={<span>›</span>}
                    onClick={onClick}
                />
                <ListRow title="Без иконки" disabled onClick={onClick} />
            </ListGroup>,
        );
        const row = screen.getByRole("button", { name: "Скачать" });
        expect(row).toHaveAccessibleDescription("Файл с данными");
        fireEvent.click(row);
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Без иконки" })).toBeDisabled();
    });
});

describe("ProgressSteps and EmptyState", () => {
    it("announces the step and renders the optional parts of an empty state", () => {
        render(
            <>
                <ProgressSteps current={1} total={2} />
                <EmptyState
                    illustration={<span>art</span>}
                    title="Пусто"
                    titleId="empty-title"
                    message="Ничего нет"
                    action={<button type="button">Действие</button>}
                />
                <EmptyState message="Только текст" />
            </>,
        );
        expect(
            screen.getByRole("progressbar", {
                name: ru.stepOf.replace("{current}", "1").replace("{total}", "2"),
            }),
        ).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Пусто" })).toHaveAttribute("id", "empty-title");
        expect(screen.getByText("art")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Действие" })).toBeInTheDocument();
        expect(screen.getByText("Только текст")).toBeInTheDocument();
    });
});

describe("Sheet", () => {
    it("closes on Escape and on backdrop press but not on panel press", () => {
        const onClose = vi.fn();
        render(
            <Sheet title="Окно" onClose={onClose} footer={<button type="button">Готово</button>}>
                <p>Содержимое</p>
            </Sheet>,
        );
        const dialog = screen.getByRole("dialog", { name: "Окно" });
        expect(dialog).toHaveFocus();
        fireEvent.mouseDown(dialog);
        expect(onClose).not.toHaveBeenCalled();
        fireEvent.keyDown(document, { key: "Enter" });
        expect(onClose).not.toHaveBeenCalled();
        fireEvent.keyDown(document, { key: "Escape" });
        expect(onClose).toHaveBeenCalledTimes(1);
        fireEvent.mouseDown(dialog.parentElement as HTMLElement);
        expect(onClose).toHaveBeenCalledTimes(2);
    });

    it("renders without a footer", () => {
        render(
            <Sheet title="Окно" onClose={() => undefined}>
                <p>Содержимое</p>
            </Sheet>,
        );
        expect(screen.getByText("Содержимое")).toBeInTheDocument();
    });
});
