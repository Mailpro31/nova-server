// Service d'activation de Nova : échange un code à usage unique contre une
// licence DÉJÀ signée. Aucune clé de signature ici, jamais.

export interface Env {
  DB: D1Database;
  ADMIN_TOKEN: string;
}

const MAX_PER_MINUTE = 10;
const encoder = new TextEncoder();

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Comparaison en temps constant : on compare les condensés, de longueur fixe.
async function sameSecret(given: string, expected: string): Promise<boolean> {
  const [a, b] = await Promise.all([sha256Hex(given), sha256Hex(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export const normalizeCode = (code: string) => code.trim().toUpperCase().replace(/[\s-]+/g, "");

async function tooMany(env: Env, ip: string, now: number): Promise<boolean> {
  const minute = Math.floor(now / 60);
  await env.DB.prepare("DELETE FROM hits WHERE minute < ?").bind(minute - 5).run();
  const row = await env.DB.prepare(
    "INSERT INTO hits (ip, minute, n) VALUES (?, ?, 1) " +
      "ON CONFLICT(ip, minute) DO UPDATE SET n = n + 1 RETURNING n",
  ).bind(ip, minute).first<{ n: number }>();
  return (row?.n ?? 1) > MAX_PER_MINUTE;
}

async function redeem(request: Request, env: Env, now: number): Promise<Response> {
  const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
  if (await tooMany(env, ip, now)) return json({ error: "rate_limited" }, 429);
  const body = (await request.json().catch(() => null)) as { code?: unknown } | null;
  if (typeof body?.code !== "string" || body.code.length > 64) return json({ error: "invalid_code" }, 400);
  const hash = await sha256Hex(normalizeCode(body.code));
  // Atomique : une seule requête peut faire passer used_at de NULL à une date.
  const row = await env.DB.prepare(
    "UPDATE codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL AND expires_at > ? RETURNING licence",
  ).bind(now, hash, now).first<{ licence: string }>();
  // Même réponse pour inconnu, déjà utilisé ou expiré : rien à deviner.
  if (!row) return json({ error: "invalid_code" }, 403);
  return json({ licence: row.licence });
}

async function addCode(request: Request, env: Env, now: number): Promise<Response> {
  const token = (request.headers.get("authorization") ?? "").replace(/^Bearer /, "");
  if (!env.ADMIN_TOKEN || !(await sameSecret(token, env.ADMIN_TOKEN))) return json({ error: "unauthorized" }, 401);
  const b = (await request.json().catch(() => null)) as Record<string, unknown> | null;
  if (
    !b || typeof b.code_hash !== "string" || !/^[0-9a-f]{64}$/.test(b.code_hash) ||
    typeof b.licence !== "string" || !b.licence || typeof b.school !== "string" ||
    typeof b.expires_at !== "number" || b.expires_at <= now || b.expires_at >= 253402300800 // an 10000 : toISOString() lèverait
  ) return json({ error: "bad_request" }, 400);
  const result = await env.DB.prepare(
    "INSERT OR IGNORE INTO codes (code_hash, licence, school, expires_at) VALUES (?, ?, ?, ?)",
  ).bind(b.code_hash, b.licence, b.school, Math.floor(b.expires_at)).run();
  return result.meta.changes ? json({ ok: true }, 201) : json({ error: "exists" }, 409);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const { pathname } = new URL(request.url);
    const now = Math.floor(Date.now() / 1000);
    if (request.method === "POST" && pathname === "/redeem") return redeem(request, env, now);
    if (request.method === "POST" && pathname === "/codes") return addCode(request, env, now);
    return json({ error: "not_found" }, 404);
  },
};
