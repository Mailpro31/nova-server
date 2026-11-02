/** L'écran des versions installées, branché sur la route du serveur. */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

describe("Client versions", () => {
  test("the client declares the route", () => {
    const client = readFileSync("src/lib/controlPlane.ts", "utf8");
    expect(client).toContain("/api/control/organization/client-versions");
  });

  test("the deployment page shows the panel", async () => {
    const page = readFileSync("src/pages/DesktopDeployment.tsx", "utf8");
    expect(page).toContain("<ClientVersions />");
    const { t } = await import("./messages");
    expect(t.deployment.versionsNote).toContain("6 hours");
  });
});
