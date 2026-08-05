import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState, ErrorState, Progress, StatusBadge } from "@/components/ui";

describe("StatusBadge", () => {
  it("maps backend statuses onto the right tone", () => {
    const { rerender } = render(<StatusBadge status="succeeded" />);
    expect(screen.getByText("succeeded").className).toContain("success");

    rerender(<StatusBadge status="failed" />);
    expect(screen.getByText("failed").className).toContain("danger");

    rerender(<StatusBadge status="running" />);
    expect(screen.getByText("running").className).toContain("warning");
  });

  it("humanises snake_case statuses", () => {
    render(<StatusBadge status="needs_review" />);
    expect(screen.getByText("needs review")).toBeInTheDocument();
  });
});

describe("Progress", () => {
  it("exposes an accessible value and clamps out-of-range input", () => {
    const { rerender } = render(<Progress value={0.42} label="ingestion" />);
    const bar = screen.getByRole("progressbar", { name: "ingestion" });
    expect(bar).toHaveAttribute("aria-valuenow", "42");

    rerender(<Progress value={1.9} label="ingestion" />);
    expect(screen.getByRole("progressbar", { name: "ingestion" })).toHaveAttribute(
      "aria-valuenow",
      "100",
    );
  });
});

describe("states", () => {
  it("renders an empty state with guidance", () => {
    render(<EmptyState title="No documents yet" description="Upload one to begin." />);
    expect(screen.getByText("No documents yet")).toBeInTheDocument();
    expect(screen.getByText("Upload one to begin.")).toBeInTheDocument();
  });

  it("surfaces the real error message instead of swallowing it", () => {
    render(<ErrorState error={new Error("Signature mismatch")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Signature mismatch");
  });
});
