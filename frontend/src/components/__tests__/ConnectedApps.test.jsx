import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccountPanel, { openLink } from "../AccountPanel";
import { api } from "../../lib/api";
import { SettingsProvider } from "../../lib/settings";

const user = { id: "u1", name: "Staff", email: "staff@corp.test", email_verified: true, mfa_enabled: false, settings: {} };
const channels = [
  { id: "whatsapp", label: "WhatsApp", enabled: true, send_to: "+91 98765 43210", approvals: true, links: [] },
  { id: "teams", label: "Microsoft Teams", enabled: false, send_to: "the Nova app", approvals: true, links: [] },
  { id: "email", label: "Email", enabled: true, send_to: "nova@corp.test", approvals: false,
    links: [{ id: "l1", channel: "email", display: "staff@corp.test", created_at: Date.now() * 1000, last_used_at: null }] },
];

function renderPanel() {
  return render(
    <SettingsProvider initial={{}}>
      <AccountPanel open onClose={() => {}} user={user} auth={{ updateUser: vi.fn(), logout: vi.fn() }} />
    </SettingsProvider>,
  );
}

describe("Connected apps", () => {
  it("lists channels, shows a link code and unlinks", async () => {
    vi.spyOn(api, "channels").mockResolvedValue({ channels });
    const code = vi.spyOn(api, "channelLinkCode").mockResolvedValue({ code: "123456", message: "link 123456", send_to: "+91 98765 43210", minutes: 10 });
    const unlink = vi.spyOn(api, "unlinkChannel").mockResolvedValue({ ok: true });
    renderPanel();
    await userEvent.click(screen.getByRole("tab", { name: "Apps" }));
    expect(await screen.findByText("Not set up on this server yet. Ask your administrator.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Link WhatsApp" }));
    expect(code).toHaveBeenCalledWith("whatsapp");
    expect(await screen.findByTestId("link-code")).toHaveTextContent("link 123456");
    expect(screen.getByRole("link", { name: /Open WhatsApp/ })).toHaveAttribute("href", "https://wa.me/919876543210?text=link%20123456");
    await userEvent.click(screen.getByRole("button", { name: "Unlink" }));
    expect(unlink).toHaveBeenCalledWith("l1");
  });

  it("builds safe deep links only for real addresses", () => {
    expect(openLink("whatsapp", "your company's Nova WhatsApp number", "link 1")).toBeNull();
    expect(openLink("email", "nova@corp.test", "link 123456")).toBe(
      "mailto:nova@corp.test?subject=Link%20my%20Nova%20account&body=link%20123456");
    expect(openLink("email", "not an address", "x")).toBeNull();
    expect(openLink("teams", "x", "y")).toBeNull();
  });
});
