import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TablePreview } from "@/components/chat/TablePreview";

describe("TablePreview", () => {
  it("renders headers and rows", () => {
    render(
      <TablePreview
        columns={["id", "name"]}
        rows={[
          [1, "Alice"],
          [2, "Bob"],
        ]}
        totalRows={2}
      />,
    );
    expect(screen.getByText("id")).toBeInTheDocument();
    expect(screen.getByText("name")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
    // The footer count is split across spans now; match on combined textContent.
    expect(
      screen.getByText(
        (_, node) => node?.textContent?.replace(/\s+/g, " ").trim() === "Показано 2 из 2",
      ),
    ).toBeTruthy();
  });

  it("renders empty marker for null cells", () => {
    render(<TablePreview columns={["x"]} rows={[[null]]} totalRows={1} />);
    expect(screen.getByText("∅")).toBeInTheDocument();
  });
});
