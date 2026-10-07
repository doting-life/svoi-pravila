import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ru } from "../localization/ru";
import { CrisisScreen } from "./CrisisScreen";
import { LimitScreen } from "./LimitScreen";

describe("LimitScreen", () => {
    it("explains the daily quota with the server message and links to help", () => {
        const onOpenCrisis = vi.fn();
        const onClose = vi.fn();
        render(
            <LimitScreen
                kind="quota"
                message="Снова будет доступно в 00:00."
                onOpenCrisis={onOpenCrisis}
                onClose={onClose}
            />,
        );
        expect(screen.getByRole("heading", { name: ru.limitTitleQuota })).toBeInTheDocument();
        expect(screen.getByText("Снова будет доступно в 00:00.")).toBeInTheDocument();
        expect(screen.getByText(ru.limitNote)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.limitHelpLink }));
        expect(onOpenCrisis).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole("button", { name: ru.limitOk }));
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("uses the overload title and omits the message when the server sent none", () => {
        render(
            <LimitScreen kind="budget" message={null} onOpenCrisis={vi.fn()} onClose={vi.fn()} />,
        );
        expect(screen.getByRole("heading", { name: ru.limitTitleBudget })).toBeInTheDocument();
        expect(screen.queryByText("Снова будет доступно в 00:00.")).not.toBeInTheDocument();
    });
});

describe("CrisisScreen", () => {
    it("shows the server lead and dialable resources", () => {
        const onBack = vi.fn();
        render(
            <CrisisScreen
                lead="SERVER_LEAD"
                resources={["8 (800) 2000-122 — детский телефон доверия", "Центр помощи — рядом"]}
                onBack={onBack}
            />,
        );
        expect(screen.getByText("SERVER_LEAD")).toBeInTheDocument();
        const dial = screen.getByRole("link", { name: /8 \(800\) 2000-122/ });
        expect(dial).toHaveAttribute("href", "tel:88002000122");
        expect(screen.getByText("Центр помощи")).toBeInTheDocument();
        expect(screen.getAllByRole("link")).toHaveLength(1);
        fireEvent.click(screen.getByRole("button", { name: ru.crisisBack }));
        expect(onBack).toHaveBeenCalledTimes(1);
    });

    it("falls back to the static support copy and 112", () => {
        render(<CrisisScreen lead={null} resources={[]} onBack={vi.fn()} />);
        expect(screen.getByText(ru.crisisSupport)).toBeInTheDocument();
        expect(
            screen.getByRole("link", { name: new RegExp(ru.crisisEmergencyTitle) }),
        ).toHaveAttribute("href", "tel:112");
        expect(screen.getByText(ru.crisisAlways)).toBeInTheDocument();
    });
});
