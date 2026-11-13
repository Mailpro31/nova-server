import { pilotAction, pilotSection } from "../pilot";
import type { Ctx, Page } from "../shell";

const act = (path: string) => (ctx: Ctx, form: FormData) =>
  pilotAction(path, form, ctx.env.DB, ctx.user, ctx.now).then((r) => r ?? new Response("Introuvable", { status: 404 }));

export const claude: Page = {
  id: "claude",
  path: "/claude",
  async render(ctx) {
    return { title: "Claude", body: await pilotSection(ctx.env.DB, ctx.now) };
  },
  actions: Object.fromEntries(["/pilot/pause", "/pilot/message", "/pilot/answer", "/pilot/hold"].map((p) => [p, act(p)])),
};
