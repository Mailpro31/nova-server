import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { CAPABILITY, can, initialSession, type SessionState } from "./session";

const PAGE = readFileSync(new URL("../pages/Provisioning.tsx", import.meta.url), "utf8");
const CONTROL = readFileSync(new URL("./controlPlane.ts", import.meta.url), "utf8");

function sessionWith(capabilities: string[]): SessionState {
  return {
    ...initialSession,
    phase: "administering",
    admin: {
      role: "it_admin",
      capabilities,
      organizationId: "organization-test",
      expiresAt: Date.now() + 60_000,
    },
  };
}

describe("SCIM provisioning surface", () => {
  test("credential mutations are capability-gated", () => {
    expect(can(sessionWith([]), CAPABILITY.provisioningManage)).toBe(false);
    expect(
      can(sessionWith(["provisioning_manage"]), CAPABILITY.provisioningManage),
    ).toBe(true);
    expect(PAGE).not.toContain('role === "organization_admin"');
    expect(PAGE).toContain("CAPABILITY.provisioningManage");
  });

  test("the one-time token never enters browser storage", () => {
    expect(PAGE).not.toContain("localStorage");
    expect(PAGE).not.toContain("sessionStorage");
    expect(PAGE).toContain("setGenerated");
    expect(PAGE).toContain("readOnly value={generated.token}");
  });

  test("GET never has a plaintext credential contract", () => {
    const statusMethod = CONTROL.slice(
      CONTROL.indexOf("provisioning(organizationId"),
      CONTROL.indexOf("createScimCredential"),
    );
    expect(statusMethod).not.toContain("token:");
    expect(CONTROL).toContain("Plaintext is present on this response only");
  });

  test("Entra setup asks only for Tenant URL and Secret Token", () => {
    expect(PAGE).toContain("view.endpoint");
    expect(PAGE).not.toContain("client_secret");
    expect(PAGE).not.toContain("password");
  });
});
