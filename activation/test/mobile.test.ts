// Console sur téléphone : barre d'onglets, feuille « Plus », application installable, protection inchangée.
import { afterEach, describe, expect, it, vi } from "vitest";
import consoleWorker from "../src/console";
import { demoGrafana, get, harness, ORIGIN } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("console mobile", () => {
  it("barre du bas : 4 rubriques + Plus relié à la feuille, sans JavaScript", async () => {
    const r = await get(await harness(demoGrafana), "/licences");
    const html = await r.text();
    expect(html).toMatch(/<nav id="tabs"[^>]*>(<a [^>]*>.*?<\/a>){4}<button [^>]*id="plus"[^>]*popovertarget="more"[^>]*aria-controls="more"/s);
    expect(html).toContain('<div id="more" popover');
    expect(html).not.toContain("<script");
    expect(r.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(r.headers.get("content-security-policy")).not.toMatch(/script-src|unsafe-eval/);
  });

  it("manifeste et icônes : servis à une personne connectée, refusés sinon", async () => {
    const h = await harness(demoGrafana);
    const m = await get(h, "/manifest.webmanifest");
    expect(m.headers.get("content-type")).toBe("application/manifest+json");
    const json = (await m.json()) as { icons: { src: string; sizes: string; purpose?: string }[] };
    expect(json.icons.map((i) => i.sizes)).toEqual(["192x192", "512x512", "512x512"]);
    expect(json.icons[2].purpose).toBe("maskable");
    for (const path of [...json.icons.map((i) => i.src), "/apple-touch-icon.png"]) {
      const icon = await get(h, path);
      expect(icon.headers.get("content-type")).toBe("image/png");
      expect(new Uint8Array(await icon.arrayBuffer()).slice(1, 4)).toEqual(new TextEncoder().encode("PNG"));
    }
    for (const path of ["/manifest.webmanifest", "/icon-192.png"]) {
      expect((await consoleWorker.fetch(new Request(`${ORIGIN}${path}`), h.env)).status).toBe(403);
    }
  });
});
