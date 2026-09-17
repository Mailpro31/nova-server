/**
 * Chaque réglage de la console s'active, se désactive, se modifie.
 *
 * Plusieurs écrans ne permettaient qu'un sens : désactiver un fournisseur
 * d'identité sans pouvoir le réactiver, retirer un profil de déploiement sans
 * pouvoir le rétablir, ni ajouter un fournisseur, ni revenir aux réglages par
 * défaut. Le serveur acceptait déjà ces gestes ; la console ne les offrait pas.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { newProviderProblems, providerPayload } from "./identity";
import type { ProviderConfigView } from "./controlPlane";

function code(path: string): string {
  return readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

const entra: ProviderConfigView = {
  id: "cfg-1",
  provider_type: "microsoft_entra",
  display_name: "Microsoft",
  enabled: false,
  entra_tenant_id: "33333333-3333-4333-8333-333333333333",
  google_hosted_domain: null,
  oidc_issuer: null,
  oidc_client_id: null,
  oidc_client_auth_method: null,
  oidc_allow_private_issuer: false,
  oidc_required_claim: null,
  oidc_required_claim_value: null,
  has_secret: false,
  validation_error: null,
};

describe("Identity model", () => {
  test("a provider is sent back whole, with only the requested change, never a secret", () => {
    const body = providerPayload(entra, { enabled: true });
    expect(body.enabled).toBe(true);
    expect(body.provider_type).toBe("microsoft_entra");
    expect(body.entra_tenant_id).toBe(entra.entra_tenant_id);
    expect("oidc_client_secret" in body).toBe(false);
  });

  test("a new provider names what it still needs, per provider type", () => {
    expect(newProviderProblems({ provider_type: "microsoft_entra" })).toEqual([
      "entra_tenant_id",
    ]);
    expect(newProviderProblems({ provider_type: "google_workspace" })).toEqual([
      "google_hosted_domain",
    ]);
    expect(
      newProviderProblems({
        provider_type: "oidc",
        oidc_issuer: "http://idp.example",
      }),
    ).toEqual(["oidc_issuer", "oidc_client_id", "oidc_client_secret"]);
    expect(
      newProviderProblems({
        provider_type: "oidc",
        oidc_issuer: "https://idp.example",
        oidc_client_id: "nova",
        oidc_client_secret: "s3cret",
      }),
    ).toEqual([]);
  });
});

describe("Identity page", () => {
  const page = code("src/pages/Identity.tsx");

  test("a provider can be added", () => {
    expect(code("src/lib/controlPlane.ts")).toContain(
      "createProviderConfig(body",
    );
    expect(page).toContain("control!.createProviderConfig(");
    expect(page).toContain("t.identity.addProvider");
  });

  test("a disabled provider can be enabled again", () => {
    expect(page).toContain("providerPayload(config, { enabled: true })");
    expect(page).toContain("t.identity.enable");
  });

  test("a provider can be edited", () => {
    expect(page).toContain("t.identity.edit");
  });
});

describe("Deployment page", () => {
  const page = code("src/pages/DesktopDeployment.tsx");

  test("a retired profile can be reactivated", () => {
    expect(page).toContain('status: "active"');
    expect(page).toContain("t.deployment.reactivate");
  });

  test("a profile's channel and update policy can be edited", () => {
    expect(page).toContain("t.deployment.edit");
  });
});

describe("Policies", () => {
  test("the organization can restore the defaults, then save", () => {
    const rest = code("src/pages/Rest.tsx");
    expect(rest).toContain("t.policies.restoreDefaults");
    expect(rest).toContain("setDraft({ ...view.defaults })");
  });

  test("a group can be reset to follow the organization", () => {
    const groups = code("src/pages/GroupPolicies.tsx");
    expect(groups).toContain("t.groupPolicies.resetGroup");
    expect(groups).toContain("setDraft({})");
  });
});
