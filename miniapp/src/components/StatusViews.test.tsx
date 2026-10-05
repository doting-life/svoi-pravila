import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ru } from "../localization/ru";
import { EmptyView, ErrorView, LoadingView } from "./StatusViews";

describe("StatusViews", () => {
    it("renders loading, empty and default error copy", () => {
        const { rerender } = render(<LoadingView />);
        expect(screen.getByText(ru.loading)).toBeInTheDocument();
        rerender(<EmptyView message="пусто" />);
        expect(screen.getByText("пусто")).toBeInTheDocument();
        rerender(<ErrorView />);
        expect(screen.getByText(ru.errorGeneric)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: ru.retry })).not.toBeInTheDocument();
    });
});
