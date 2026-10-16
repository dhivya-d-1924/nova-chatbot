import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuthScreen from "../AuthScreen";
import { api } from "../../lib/api";

function makeAuth(overrides = {}) {
  return { login: vi.fn(), loginMfa: vi.fn(), register: vi.fn(), resetPassword: vi.fn(), ...overrides };
}

describe("AuthScreen", () => {
  it("validates fields before calling the server", async () => {
    vi.spyOn(api, "authConfig").mockResolvedValue({ registration_open: true });
    const auth = makeAuth();
    render(<AuthScreen auth={auth} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(auth.login).not.toHaveBeenCalled();
  });

  it("moves to the two-step code screen when the server asks for it", async () => {
    vi.spyOn(api, "authConfig").mockResolvedValue({ registration_open: true });
    const auth = makeAuth({ login: vi.fn().mockResolvedValue({ mfa: "tok" }), loginMfa: vi.fn().mockResolvedValue({ ok: true }) });
    render(<AuthScreen auth={auth} />);
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Two-step verification" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Verification code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(auth.loginMfa).toHaveBeenCalledWith("tok", "123456");
  });

  it("offers to resend the verification link", async () => {
    vi.spyOn(api, "authConfig").mockResolvedValue({ registration_open: true });
    const err = Object.assign(new Error("Verify your email address first."), { code: "email_unverified" });
    const auth = makeAuth({ login: vi.fn().mockRejectedValue(err) });
    const resend = vi.spyOn(api, "resendVerification").mockResolvedValue({ message: "Sent again." });
    render(<AuthScreen auth={auth} />);
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send the link again" }));
    expect(resend).toHaveBeenCalledWith("a@example.com");
    expect(await screen.findByText("Sent again.")).toBeInTheDocument();
  });

  it("hides sign-up when registration is closed", async () => {
    vi.spyOn(api, "authConfig").mockResolvedValue({ registration_open: false });
    render(<AuthScreen auth={makeAuth()} />);
    expect(await screen.findByText("New accounts are created by an administrator.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create an account" })).toBeNull();
  });

  it("toggles password visibility", async () => {
    vi.spyOn(api, "authConfig").mockResolvedValue({ registration_open: true });
    render(<AuthScreen auth={makeAuth()} />);
    const pw = screen.getByLabelText("Password");
    expect(pw).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(pw).toHaveAttribute("type", "text");
  });
});
