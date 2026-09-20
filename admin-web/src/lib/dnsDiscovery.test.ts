/**
 * L'enregistrement DNS qui permet de trouver l'organisation par e-mail.
 *
 * La console le montre prêt à coller et sait le vérifier : personne ne devrait
 * avoir à le composer à la main, ni à deviner s'il est bien publié.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

function code(path: string): string {
  return readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

describe("DNS discovery client", () => {
  test("both routes are declared", () => {
    const client = code("src/lib/controlPlane.ts");
    expect(client).toContain("discoveryDns(organizationId: string)");
    expect(client).toContain("checkDiscoveryDns(organizationId: string, domain: string)");
    expect(client).toContain("/discovery/dns");
  });
});

describe("DNS discovery panel", () => {
  const page = code("src/pages/DesktopDeployment.tsx");

  test("the record is shown and can be checked", async () => {
    const { t } = await import("./messages");
    expect(page).toContain("control!.checkDiscoveryDns(");
    expect(page).toContain("t.dns.title");
    expect(page).toContain("record.value");
    expect(t.dns.check.length).toBeGreaterThan(0);
  });

  test("what the student types is explained, not the URL", async () => {
    const { t } = await import("./messages");
    expect(t.dns.lead.toLowerCase()).toContain("email");
    expect(t.dns.lead.toLowerCase()).not.toContain("https://");
  });

  test("each outcome of the check has its own sentence", async () => {
    const { t } = await import("./messages");
    expect(t.dns.statusMatch.toLowerCase()).toContain("published");
    expect(t.dns.statusMissing.toLowerCase()).toContain("not");
    expect(t.dns.statusMismatch.toLowerCase()).toContain("another");
    // Une panne de DNS n'est pas une absence d'enregistrement.
    expect(t.dns.unavailable.toLowerCase()).toContain("could not");
  });

  test("a missing service endpoint is explained before anything else", async () => {
    const { t } = await import("./messages");
    expect(t.dns.endpointMissing.toLowerCase()).toContain("service endpoint");
    expect(page).toContain("t.dns.endpointMissing");
  });

  test("server refusals are explained", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("server", "DNS_UNAVAILABLE")).toBe(t.dns.unavailable);
    expect(errorMessage("invalid", "DOMAIN_NOT_SERVED")).toBe(t.dns.domainNotServed);
  });
});
