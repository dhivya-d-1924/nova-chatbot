import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatMessage from "../ChatMessage";

const base = { created_at: Date.now() * 1000, rating: 0 };
const noop = () => {};

describe("ChatMessage", () => {
  it("renders Markdown but never raw HTML or script links", () => {
    const content = "**bold** <img src=x onerror=alert(1)> [bad](javascript:alert(1)) [good](https://example.com)";
    const { container } = render(<ChatMessage message={{ ...base, id: "a1", role: "assistant", content }} onRate={noop} onRegenerate={noop} onResolve={noop} />);
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(container.querySelector("img")).toBeNull();
    const links = [...container.querySelectorAll("a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://example.com"]);
    expect(links[0]).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("lets the user edit their own message", async () => {
    const onEdit = vi.fn();
    render(<ChatMessage message={{ ...base, id: "u1", role: "user", content: "old text" }} canEdit onEdit={onEdit} onRate={noop} onRegenerate={noop} onResolve={noop} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByLabelText("Edit your message");
    await userEvent.clear(box);
    await userEvent.type(box, "new text");
    await userEvent.click(screen.getByRole("button", { name: "Save & send" }));
    expect(onEdit).toHaveBeenCalledWith("u1", "new text");
  });

  it("shows an approval card and reports the decision", async () => {
    const onResolve = vi.fn();
    const steps = [{ id: "s1", tool: "send_email", status: "awaiting", label: "Send email to bob@example.com", pending_id: "p1",
      args: { to: "bob@example.com", subject: "Hi", body: "Hello" }, expires_at: (Date.now() + 600000) * 1000 }];
    render(<ChatMessage message={{ ...base, id: "a2", role: "assistant", content: "Waiting", steps }} onResolve={onResolve} onRate={noop} onRegenerate={noop} />);
    expect(screen.getByText("bob@example.com", { selector: "dd" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onResolve).toHaveBeenCalledWith("a2", "p1", true);
  });

  it("disables approval once the request has expired", () => {
    const steps = [{ id: "s1", tool: "send_email", status: "awaiting", label: "Send email", pending_id: "p1", args: {}, expires_at: 1 }];
    render(<ChatMessage message={{ ...base, id: "a3", role: "assistant", content: "Waiting", steps }} onResolve={noop} onRate={noop} onRegenerate={noop} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText(/expired/)).toBeInTheDocument();
  });

  it("shows citations and reveals the passage", async () => {
    const sources = [{ n: 1, filename: "policy.pdf", kind: "pdf", page: 3, snippet: "Refunds within 30 days." }];
    render(<ChatMessage message={{ ...base, id: "a4", role: "assistant", content: "Yes [1]", sources }} onResolve={noop} onRate={noop} onRegenerate={noop} />);
    await userEvent.click(screen.getByRole("button", { name: /policy\.pdf/ }));
    expect(screen.getByText("Refunds within 30 days.")).toBeInTheDocument();
  });

  it("has a timestamp with a machine-readable date", () => {
    const { container } = render(<ChatMessage message={{ ...base, id: "a5", role: "assistant", content: "Hi" }} onResolve={noop} onRate={noop} onRegenerate={noop} />);
    expect(container.querySelector("time")).toHaveAttribute("datetime");
  });
});
