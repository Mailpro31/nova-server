import { claimsOf, invalid, newCode, signLicence, type LicenceRequest } from "../licence";
import { escape } from "../dashboard";
import { normalizeCode, sha256Hex } from "../index";
import { audit, layout, notice, pill, type Ctx, type Page, type View } from "../shell";

const DAY = 86400;
const day = (seconds: number | null) => (seconds ? new Date(seconds * 1000).toISOString().slice(0, 10) : "");

async function view(ctx: Ctx, message = ""): Promise<View> {
  const { results } = await ctx.env.DB.prepare(
    "SELECT school, licence, expires_at, used_at FROM codes ORDER BY expires_at DESC LIMIT 200",
  ).all<{ school: string; licence: string; expires_at: number; used_at: number | null }>();
  const rows = results.map((row) => {
    const c = claimsOf(row.licence);
    const state = row.used_at ? pill("ok", `Activé le ${day(row.used_at)}`)
      : row.expires_at > ctx.now ? pill("warn", `En attente · jusqu'au ${day(row.expires_at)}`) : pill("neutral", "Code expiré");
    return `<tr><td data-label="Établissement"><strong>${escape(row.school)}</strong></td><td data-label="Offre">${escape(c.tier)}</td><td data-label="Type">${escape(c.kind === "pilot" ? "Pilote" : "Abonnement")}</td><td data-label="Licence">${escape(c.starts)} → ${escape(c.ends)}</td><td data-label="État">${state}</td></tr>`;
  }).join("");
  return {
    title: "Licences",
    notice: message,
    body: `<div class="grid"><article class="card"><header><h3>Nouveau code d'activation</h3></header><p class="muted">À usage unique : la DSI le colle dans NOVA_ACTIVATION_CODE.</p>
<form method="post" action="/codes">
<label>Établissement<input name="organization" required maxlength="100"></label>
<label>Offre (tranche)<input name="tier" required maxlength="40" placeholder="151-250"></label>
<label>Type<select name="kind"><option value="pilot">Pilote</option><option value="subscription">Abonnement</option></select></label>
<label>Début<input name="starts" type="date" required></label>
<label>Fin<input name="ends" type="date" required></label>
<label>Enveloppe d'IA (€, facultatif)<input name="ai_envelope_eur" type="number" min="0" step="1"></label>
<label>Code valable (jours)<input name="code_days" type="number" min="1" max="90" value="30"></label>
<button type="submit">Créer le code</button></form></article>
<article class="card"><header><h3>Codes donnés</h3></header><div class="scroll"><table class="cards"><thead><tr><th>Établissement</th><th>Offre</th><th>Type</th><th>Licence</th><th>État</th></tr></thead>
<tbody>${rows || `<tr><td colspan="5" class="muted">Aucun code pour l'instant.</td></tr>`}</tbody></table></div></article></div>`,
  };
}

async function create(ctx: Ctx, form: FormData): Promise<Response> {
  const text = (name: string) => String(form.get(name) ?? "");
  const envelope = text("ai_envelope_eur").trim();
  const r: LicenceRequest = {
    organization: text("organization"), tier: text("tier"), kind: text("kind"), starts: text("starts"), ends: text("ends"),
    ...(envelope ? { ai_envelope_eur: Number(envelope) } : {}),
  };
  const days = Math.min(90, Math.max(1, Math.floor(Number(text("code_days")) || 30)));
  const show = async (message: string) => layout(ctx, "licences", await view(ctx, message), {});
  const problem = invalid(r);
  if (problem) return show(notice(escape(problem), "bad"));
  if (!ctx.env.SIGNING_KEY) return show(notice("Clé de signature absente (secret SIGNING_KEY).", "bad"));
  const code = newCode();
  await ctx.env.DB.prepare("INSERT INTO codes (code_hash, licence, school, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await sha256Hex(normalizeCode(code)), await signLicence(ctx.env.SIGNING_KEY, r), r.organization.trim(), ctx.now + days * DAY)
    .run();
  await audit(ctx.env.DB, ctx.user, ctx.now, "Code créé", `${r.organization.trim()} · ${r.kind} · ${r.starts} → ${r.ends}`);
  return show(notice(`Code pour ${escape(r.organization.trim())}, valable ${days} jours, à donner à la DSI :<br><code class="big">${code}</code><br>Il ne sera plus affiché.`, "ok"));
}

export const licences: Page = {
  id: "licences",
  path: "/licences",
  render: (ctx) => view(ctx),
  actions: { "/codes": create },
};
