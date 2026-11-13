import { pilotAction, pilotView } from "../pilot";
import type { Ctx, Page } from "../shell";

const act = (path: string) => (ctx: Ctx, form: FormData) =>
  pilotAction(path, form, ctx.env.DB, ctx.user, ctx.now).then((r) => r ?? new Response("Introuvable", { status: 404 }));

export const claude: Page = {
  id: "claude",
  path: "/claude",
  render: pilotView,
  actions: Object.fromEntries(["/pilot/pause", "/pilot/message", "/pilot/answer", "/pilot/hold"].map((p) => [p, act(p)])),
  css: `.top{display:grid;grid-template-columns:minmax(280px,1fr) 2fr;gap:14px;margin-bottom:22px;align-items:start}
.top .kpis{margin:0;grid-template-columns:1fr 1fr}
.seg.wrap{flex-wrap:wrap}
@media (max-width:760px){.top{grid-template-columns:1fr}}`,
};
