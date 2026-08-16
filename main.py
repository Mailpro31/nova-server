"""
Nova Server — MVP + panneau d'administration
Serveur de transcription + reformulation on-premise pour établissements.
Auth : email académique + code à 6 chiffres.
Confidentialité : seuls des COMPTEURS d'usage sont stockés, jamais le contenu.
"""
import csv
import io
import os
import random
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, EmailStr

# ---------------------------------------------------------------- config
DB_PATH = Path(os.getenv("NOVA_DB", "nova.db"))
ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("NOVA_DOMAINS", "eleves.ipsa.fr,ipsa.fr").split(",")]
MAX_SEATS = int(os.getenv("NOVA_MAX_SEATS", "300"))
MAX_MACHINES_PER_EMAIL = int(os.getenv("NOVA_MAX_MACHINES", "3"))
CODE_TTL_SECONDS = 600          # code email valide 10 min
CODE_MAX_ATTEMPTS = 5
REQUIRE_AUTH = os.getenv("NOVA_REQUIRE_AUTH", "true").lower() == "true"
TOKEN_TTL_SECONDS = int(os.getenv("NOVA_TOKEN_TTL_SECONDS", str(30 * 24 * 3600)))

# Configuration publique Campus. Les valeurs restent sobres et génériques par
# défaut ; un établissement peut les remplacer sans modifier le client.
ORGANIZATION_ID = os.getenv("NOVA_ORGANIZATION_ID", "ipsa")
ORGANIZATION_NAME = os.getenv("NOVA_ORGANIZATION_NAME", "IPSA")
ORGANIZATION_SHORT_NAME = os.getenv("NOVA_ORGANIZATION_SHORT_NAME", "IPSA")
ORGANIZATION_CAMPUS = os.getenv("NOVA_ORGANIZATION_CAMPUS", "Paris")
EDUCATION_MODE = os.getenv("NOVA_EDUCATION_MODE", "normal").lower()
AI_SKILLS_REQUIRED = os.getenv("NOVA_AI_SKILLS_REQUIRED", "false").lower() == "true"
AI_SKILLS_TRACK_PROGRESS = os.getenv("NOVA_AI_SKILLS_TRACK_PROGRESS", "true").lower() == "true"

# Microsoft Entra ID utilise le flux OAuth 2.0 Device Code : adapté à une app
# desktop, sans secret client embarqué. La méthode n'est annoncée que lorsque
# l'identifiant d'application est configuré côté serveur.
ENTRA_TENANT_ID = os.getenv("NOVA_ENTRA_TENANT_ID", "organizations")
ENTRA_CLIENT_ID = os.getenv("NOVA_ENTRA_CLIENT_ID", "")
ENTRA_SCOPES = os.getenv("NOVA_ENTRA_SCOPES", "openid profile email User.Read")
ENTRA_AUTHORITY = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/oauth2/v2.0"
ENTRA_FLOWS: dict[str, dict] = {}

# Envoi d'emails : par défaut, mode DEV (le code est affiché dans les logs serveur)
SMTP_HOST = os.getenv("NOVA_SMTP_HOST", "")          # ex. smtp.ipsa.fr
SMTP_PORT = int(os.getenv("NOVA_SMTP_PORT", "587"))
SMTP_USER = os.getenv("NOVA_SMTP_USER", "")
SMTP_PASS = os.getenv("NOVA_SMTP_PASS", "")
SMTP_FROM = os.getenv("NOVA_SMTP_FROM", "nova@ipsa.fr")

# Moteurs d'inférence (services du docker-compose)
WHISPER_URL = os.getenv("NOVA_WHISPER_URL", "http://whisper:8000")   # faster-whisper-server
LLM_URL = os.getenv("NOVA_LLM_URL", "http://llm:8000/v1")            # vLLM (API OpenAI)
LLM_MODEL = os.getenv("NOVA_LLM_MODEL", "Qwen/Qwen2.5-14B-Instruct")

ADMIN_TOKEN = os.getenv("NOVA_ADMIN_TOKEN", secrets.token_urlsafe(24))

# ---------------------------------------------------------------- db
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS auth_codes(
            email TEXT, code TEXT, expires_at REAL, attempts INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tokens(
            token TEXT PRIMARY KEY, email TEXT, machine TEXT,
            active INTEGER DEFAULT 1, created_at REAL, expires_at REAL
        );
        CREATE TABLE IF NOT EXISTS users(
            email TEXT PRIMARY KEY,
            role TEXT DEFAULT 'student',       -- student | teacher | staff | partner
            cohort TEXT DEFAULT '',            -- ex. "Promo 2028"
            note TEXT DEFAULT '',              -- note libre de l'admin
            disabled INTEGER DEFAULT 0,        -- compte suspendu
            created_at REAL,
            last_seen REAL
        );
        -- compteurs d'usage UNIQUEMENT (jamais de contenu audio/texte)
        CREATE TABLE IF NOT EXISTS usage(
            email TEXT, day TEXT,
            transcriptions INTEGER DEFAULT 0,
            reformulations INTEGER DEFAULT 0,
            audio_seconds REAL DEFAULT 0,
            PRIMARY KEY (email, day)
        );
        CREATE TABLE IF NOT EXISTS shared_dictionary(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL UNIQUE,
            replacement TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS personal_dictionary(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            term TEXT NOT NULL,
            replacement TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'personal',
            UNIQUE(email, term)
        );
        CREATE TABLE IF NOT EXISTS snippets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            trigger TEXT NOT NULL,
            content TEXT NOT NULL,
            UNIQUE(email, trigger)
        );
        CREATE TABLE IF NOT EXISTS formatting_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            rule TEXT NOT NULL,
            shared INTEGER NOT NULL DEFAULT 0
        );
        """)
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(tokens)").fetchall()
        }
        if "expires_at" not in columns:
            conn.execute("ALTER TABLE tokens ADD COLUMN expires_at REAL")
init_db()

def touch_user(email: str):
    """Crée le profil s'il n'existe pas + met à jour last_seen."""
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(email, created_at) VALUES (?,?)",
            (email, time.time()),
        )
        conn.execute("UPDATE users SET last_seen=? WHERE email=?", (time.time(), email))

def count_usage(email: str, kind: str, audio_seconds: float = 0.0):
    day = time.strftime("%Y-%m-%d")
    col = "transcriptions" if kind == "transcription" else "reformulations"
    with db() as conn:
        conn.execute(
            f"""INSERT INTO usage(email, day, {col}, audio_seconds) VALUES (?,?,?,?)
                ON CONFLICT(email, day) DO UPDATE SET
                {col} = {col} + 1, audio_seconds = audio_seconds + ?""",
            (email, day, 1, audio_seconds, audio_seconds),
        )

# ---------------------------------------------------------------- email
def send_code_email(to: str, code: str):
    if not SMTP_HOST:
        print(f"[DEV] Code pour {to} : {code}")
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, to, "Votre code Nova"
    msg.set_content(f"Votre code de connexion Nova : {code}\n\nIl expire dans 10 minutes.")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

# ---------------------------------------------------------------- auth helpers
def current_user(authorization: str = Header(default="")) -> str:
    if not REQUIRE_AUTH:
        return "dev"
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token manquant")
    token = authorization.removeprefix("Bearer ").strip()
    with db() as conn:
        row = conn.execute(
            """SELECT t.email, t.expires_at, u.disabled FROM tokens t
               LEFT JOIN users u ON u.email = t.email
               WHERE t.token=? AND t.active=1""", (token,)
        ).fetchone()
    if not row:
        raise HTTPException(401, "Token invalide ou révoqué")
    if row["disabled"]:
        raise HTTPException(403, "Compte suspendu — contactez votre administrateur")
    if row["expires_at"] and row["expires_at"] < time.time():
        with db() as conn:
            conn.execute("UPDATE tokens SET active=0 WHERE token=?", (token,))
        raise HTTPException(401, "Session expiree")
    touch_user(row["email"])
    return row["email"]

def issue_token(email: str, machine: str) -> dict:
    """Create a time-limited Nova session for a verified user."""
    email = email.lower()
    now = time.time()
    with db() as conn:
        # Reconnecting the same workstation replaces its previous session
        # instead of consuming another device slot.
        conn.execute(
            "UPDATE tokens SET active=0 WHERE email=? AND machine=?",
            (email, machine),
        )
        already_active = conn.execute(
            "SELECT 1 FROM tokens WHERE email=? AND active=1 AND "
            "(expires_at IS NULL OR expires_at>?) LIMIT 1",
            (email, now),
        ).fetchone()
        seats = conn.execute(
            "SELECT COUNT(DISTINCT email) c FROM tokens WHERE active=1 AND "
            "(expires_at IS NULL OR expires_at>?)", (now,)
        ).fetchone()["c"]
        if not already_active and seats >= MAX_SEATS:
            raise HTTPException(403, "Nombre maximal d'utilisateurs atteint")
        machines = conn.execute(
            "SELECT COUNT(*) c FROM tokens WHERE email=? AND active=1 AND "
            "(expires_at IS NULL OR expires_at>?)", (email, now)
        ).fetchone()["c"]
        if machines >= MAX_MACHINES_PER_EMAIL:
            raise HTTPException(403, "Nombre maximal d'appareils atteint")
        token = secrets.token_urlsafe(32)
        expires_at = now + TOKEN_TTL_SECONDS
        conn.execute(
            "INSERT INTO tokens(token,email,machine,active,created_at,expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (token, email, machine, 1, now, expires_at),
        )
    touch_user(email)
    return {"token": token, "expires_in": TOKEN_TTL_SECONDS}

def authenticated_token(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token manquant")
    return authorization.removeprefix("Bearer ").strip()

def require_admin(x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "Admin invalide")
    return True

# ---------------------------------------------------------------- schemas
class RequestCode(BaseModel):
    email: EmailStr
    machine: str = "poste inconnu"

class VerifyCode(BaseModel):
    email: EmailStr
    code: str
    machine: str = "poste inconnu"

class ReformulateRequest(BaseModel):
    text: str
    style_prompt: str = "Reformule le texte dicté en un texte clair et professionnel. Conserve la langue d'origine."

class UserUpdate(BaseModel):
    role: str | None = None
    cohort: str | None = None
    note: str | None = None
    disabled: bool | None = None

class DictionaryEntryRequest(BaseModel):
    term: str
    replacement: str

class DictionaryLearnRequest(BaseModel):
    heard: str
    corrected: str

class SnippetRequest(BaseModel):
    trigger: str
    content: str

class FormattingRuleRequest(BaseModel):
    rule: str

class CommandRequest(BaseModel):
    instruction: str
    text: str

class EntraStartRequest(BaseModel):
    machine: str = "poste inconnu"

class EntraPollRequest(BaseModel):
    flow_id: str

AI_SKILLS = [
    {
        "id": "working-with-ai",
        "title": "Working with AI",
        "summary": "Define the goal, context and expected output before asking AI.",
        "practice": "Rewrite one vague request with a goal, useful context and an output format.",
        "duration_minutes": 4,
    },
    {
        "id": "ask-better",
        "title": "Ask better",
        "summary": "Give constraints, examples and a precise success criterion.",
        "practice": "Add two constraints and one example to a prompt you use for class.",
        "duration_minutes": 5,
    },
    {
        "id": "verify-output",
        "title": "Verifying AI outputs",
        "summary": "Check sources, calculations and assumptions before reuse.",
        "practice": "List three claims in an AI answer that would require an independent check.",
        "duration_minutes": 5,
    },
    {
        "id": "confidential-information",
        "title": "Confidential information",
        "summary": "Know what must not be sent to an unauthorized system.",
        "practice": "Remove credentials and unnecessary personal or confidential data from a sample prompt.",
        "duration_minutes": 4,
    },
    {
        "id": "ai-in-engineering",
        "title": "AI in engineering",
        "summary": "Use AI as an engineering tool, not a replacement for judgment.",
        "practice": "List the assumptions and checks you would own after an AI-assisted trade study.",
        "duration_minutes": 5,
    },
    {
        "id": "responsible-use",
        "title": "Responsible use",
        "summary": "Recognize when AI use is appropriate, limited or prohibited.",
        "practice": "Check the applicable course policy before using AI on an assessed task.",
        "duration_minutes": 3,
    },
]

def public_campus_config() -> dict:
    auth_methods = ["email_code"]
    if ENTRA_CLIENT_ID:
        auth_methods.append("entra")
    return {
        "organization": {
            "id": ORGANIZATION_ID,
            "name": ORGANIZATION_NAME,
            "shortName": ORGANIZATION_SHORT_NAME,
            "campusName": ORGANIZATION_CAMPUS,
            "managed": True,
        },
        "auth_methods": auth_methods,
        "education_mode": EDUCATION_MODE if EDUCATION_MODE in {"normal", "classroom", "assessment"} else "normal",
        "ai_skills": {
            "enabled": EDUCATION_MODE != "assessment",
            "required": AI_SKILLS_REQUIRED,
            "trackProgress": AI_SKILLS_TRACK_PROGRESS,
        },
        "capabilities": {
            "dictation": True,
            "rewrite": EDUCATION_MODE != "assessment",
            "styles": EDUCATION_MODE != "assessment",
            "fileTranscription": EDUCATION_MODE != "assessment",
            "commands": EDUCATION_MODE == "normal",
            "dictionary": EDUCATION_MODE != "assessment",
            "snippets": EDUCATION_MODE != "assessment",
            "formattingRules": EDUCATION_MODE != "assessment",
            "screenContext": False,
            "cloudInference": True,
            "engineeringNotes": EDUCATION_MODE == "normal",
            "aiSkills": True,
            "personalization": True,
        },
        "privacy": {
            "verified": True,
            "contentRetention": "not_stored",
            "usageCounters": "counts_only",
            "infrastructure": "campus",
        },
    }

# ---------------------------------------------------------------- app
app = FastAPI(title="Nova Server", version="0.2.0")

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "domains": ALLOWED_DOMAINS,
        "auth_methods": public_campus_config()["auth_methods"],
        "version": app.version,
    }

@app.get("/api/config")
def campus_config():
    """Public, non-secret organization policy consumed by Nova Desktop."""
    return public_campus_config()

# --- auth : demande de code
@app.post("/api/auth/request")
def auth_request(body: RequestCode):
    email = body.email.lower()
    domain = email.split("@")[-1]
    if domain not in ALLOWED_DOMAINS:
        return {"sent": True}   # réponse identique : ne pas révéler les domaines
    with db() as conn:
        user = conn.execute("SELECT disabled FROM users WHERE email=?", (email,)).fetchone()
    if user and user["disabled"]:
        return {"sent": True}   # compte suspendu : on ne dit rien
    code = f"{random.randint(0, 999999):06d}"
    with db() as conn:
        conn.execute("DELETE FROM auth_codes WHERE email=?", (email,))
        conn.execute("INSERT INTO auth_codes VALUES (?,?,?,0)",
                     (email, code, time.time() + CODE_TTL_SECONDS))
    try:
        send_code_email(email, code)
    except Exception as e:
        print(f"[SMTP] erreur envoi à {email}: {e}")
    return {"sent": True}

# --- auth : vérification du code -> token
@app.post("/api/auth/verify")
def auth_verify(body: VerifyCode):
    email = body.email.lower()
    with db() as conn:
        row = conn.execute("SELECT * FROM auth_codes WHERE email=?", (email,)).fetchone()
        if not row or row["expires_at"] < time.time():
            raise HTTPException(400, "Code expiré, redemandez-en un")
        if row["attempts"] >= CODE_MAX_ATTEMPTS:
            raise HTTPException(429, "Trop de tentatives, redemandez un code")
        if row["code"] != body.code.strip():
            conn.execute("UPDATE auth_codes SET attempts=attempts+1 WHERE email=?", (email,))
            raise HTTPException(400, "Code incorrect")

        conn.execute("DELETE FROM auth_codes WHERE email=?", (email,))
    return issue_token(email, body.machine)

@app.post("/api/auth/logout")
def auth_logout(
    authorization: str = Header(default=""),
    user: str = Depends(current_user),
):
    token = authenticated_token(authorization)
    with db() as conn:
        conn.execute(
            "UPDATE tokens SET active=0 WHERE token=? AND email=?", (token, user)
        )
    return {"revoked": True}

@app.post("/api/auth/entra/start")
async def entra_start(body: EntraStartRequest):
    if not ENTRA_CLIENT_ID:
        raise HTTPException(503, "Microsoft Entra ID n'est pas configure")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{ENTRA_AUTHORITY}/devicecode",
            data={"client_id": ENTRA_CLIENT_ID, "scope": ENTRA_SCOPES},
        )
    if response.status_code != 200:
        raise HTTPException(502, "Microsoft Entra ID est indisponible")
    payload = response.json()
    flow_id = secrets.token_urlsafe(24)
    ENTRA_FLOWS[flow_id] = {
        "device_code": payload["device_code"],
        "machine": body.machine,
        "expires_at": time.time() + int(payload.get("expires_in", 900)),
    }
    return {
        "flow_id": flow_id,
        "user_code": payload["user_code"],
        "verification_uri": payload.get("verification_uri"),
        "verification_uri_complete": payload.get("verification_uri_complete"),
        "expires_in": payload.get("expires_in", 900),
        "interval": payload.get("interval", 5),
        "message": payload.get("message", ""),
    }

@app.post("/api/auth/entra/poll")
async def entra_poll(body: EntraPollRequest):
    flow = ENTRA_FLOWS.get(body.flow_id)
    if not flow or flow["expires_at"] < time.time():
        ENTRA_FLOWS.pop(body.flow_id, None)
        raise HTTPException(400, "Connexion Microsoft expiree")
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            f"{ENTRA_AUTHORITY}/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": ENTRA_CLIENT_ID,
                "device_code": flow["device_code"],
            },
        )
        token_payload = token_response.json()
        if token_response.status_code != 200:
            error = token_payload.get("error", "authorization_pending")
            if error in {"authorization_pending", "slow_down"}:
                return {"status": "pending", "retry_after": 5}
            ENTRA_FLOWS.pop(body.flow_id, None)
            raise HTTPException(400, token_payload.get("error_description", error))
        graph_response = await client.get(
            "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
        )
    if graph_response.status_code != 200:
        raise HTTPException(502, "Profil Microsoft inaccessible")
    graph_profile = graph_response.json()
    email = (graph_profile.get("mail") or graph_profile.get("userPrincipalName") or "").lower()
    if not email or email.split("@")[-1] not in ALLOWED_DOMAINS:
        raise HTTPException(403, "Compte Microsoft hors etablissement")
    ENTRA_FLOWS.pop(body.flow_id, None)
    session = issue_token(email, flow["machine"])
    return {"status": "complete", "email": email, **session}

# --- profil de l'utilisateur connecté (pour l'app)
@app.get("/api/me")
def me(user: str = Depends(current_user)):
    with db() as conn:
        u = conn.execute("SELECT email, role, cohort FROM users WHERE email=?", (user,)).fetchone()
    return dict(u) if u else {"email": user}

def clean_required(value: str, field: str, limit: int = 4000) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(400, f"{field} est requis")
    if len(cleaned) > limit:
        raise HTTPException(400, f"{field} est trop long")
    return cleaned

def writing_context(user: str) -> tuple[str, list[sqlite3.Row]]:
    """Return a compact prompt addition and snippets without storing content."""
    with db() as conn:
        shared = conn.execute(
            "SELECT term,replacement FROM shared_dictionary ORDER BY term LIMIT 200"
        ).fetchall()
        personal = conn.execute(
            "SELECT term,replacement FROM personal_dictionary WHERE email=? "
            "ORDER BY term LIMIT 200", (user,)
        ).fetchall()
        rules = conn.execute(
            "SELECT rule FROM formatting_rules WHERE shared=1 OR email=? "
            "ORDER BY shared DESC,id LIMIT 100", (user,)
        ).fetchall()
        snippets = conn.execute(
            "SELECT trigger,content FROM snippets WHERE email=? ORDER BY trigger", (user,)
        ).fetchall()
    vocabulary = [*shared, *personal]
    additions = []
    if vocabulary:
        additions.append(
            "Preferred vocabulary:\n" + "\n".join(
                f"- {row['term']} -> {row['replacement']}" for row in vocabulary
            )
        )
    if rules:
        additions.append(
            "Writing rules:\n" + "\n".join(f"- {row['rule']}" for row in rules)
        )
    return "\n\n".join(additions), snippets

def apply_snippets(text: str, snippets: list[sqlite3.Row]) -> str:
    result = text
    for row in snippets:
        result = re.sub(
            rf"\b{re.escape(row['trigger'])}\b",
            lambda _: row["content"],
            result,
            flags=re.IGNORECASE,
        )
    return result

def vocabulary_context(user: str) -> str:
    """Vocabulary alone: shared dictionary + the user's personal entries.

    `writing_context` is the dictation context — vocabulary *and* writing rules,
    with snippets applied to the text. An AI Skill needs neither: see
    `command_prompt`.
    """
    with db() as conn:
        shared = conn.execute(
            "SELECT term,replacement FROM shared_dictionary ORDER BY term LIMIT 200"
        ).fetchall()
        personal = conn.execute(
            "SELECT term,replacement FROM personal_dictionary WHERE email=? "
            "ORDER BY term LIMIT 200", (user,)
        ).fetchall()
    return "\n".join(
        f"- {row['term']} -> {row['replacement']}" if row["replacement"]
        else f"- {row['term']}"
        for row in [*shared, *personal]
    )

def command_prompt(instruction: str, vocabulary: str) -> str:
    """System prompt of an AI Skill.

    The vocabulary is injected as CONTEXT, never by preprocessing `text`: the
    text the user selected is forwarded as-is. Without it, "Improve" or
    "Translate" respelled the school's acronyms and proper nouns the way the
    model saw fit.

    Deliberately ABSENT, both meaningless on already-written text:

    * voice shortcuts — they replace a SPOKEN phrase; expanding them here would
      rewrite wording the user typed on purpose;
    * formatting rules — meant for shaping a dictation. The server only receives
      a free-form instruction and cannot tell which Skill is running: forcing
      "always sign with your full name" onto an explanation would corrupt it.
      To be revisited once the Skill type is part of the request.
    """
    prompt = (
        "Execute the user's editing instruction. Return only the transformed text. "
        "Preserve facts and the original language.\n\nInstruction: " + instruction
    )
    if vocabulary:
        prompt += (
            "\n\nPreferred vocabulary, to spell exactly this way if these terms "
            "appear in the result:\n" + vocabulary
        )
    return prompt

@app.get("/api/vocabulary")
def vocabulary(user: str = Depends(current_user)):
    with db() as conn:
        shared = conn.execute(
            "SELECT id,term,replacement FROM shared_dictionary ORDER BY term"
        ).fetchall()
        personal = conn.execute(
            "SELECT id,term,replacement,source FROM personal_dictionary "
            "WHERE email=? ORDER BY term", (user,)
        ).fetchall()
        snippets = conn.execute(
            "SELECT id,trigger,content FROM snippets WHERE email=? ORDER BY trigger",
            (user,),
        ).fetchall()
    return {
        "shared": [dict(row) for row in shared],
        "personal": [dict(row) for row in personal],
        "snippets": [dict(row) for row in snippets],
    }

@app.post("/api/dictionary")
def dictionary_add(body: DictionaryEntryRequest, user: str = Depends(current_user)):
    term = clean_required(body.term, "term", 200)
    replacement = clean_required(body.replacement, "replacement", 500)
    with db() as conn:
        conn.execute(
            "INSERT INTO personal_dictionary(email,term,replacement,source) VALUES (?,?,?,'personal') "
            "ON CONFLICT(email,term) DO UPDATE SET replacement=excluded.replacement,source='personal'",
            (user, term, replacement),
        )
        row = conn.execute(
            "SELECT id FROM personal_dictionary WHERE email=? AND term=?", (user, term)
        ).fetchone()
    return {"id": row["id"]}

@app.delete("/api/dictionary/{entry_id}")
def dictionary_delete(entry_id: int, user: str = Depends(current_user)):
    with db() as conn:
        deleted = conn.execute(
            "DELETE FROM personal_dictionary WHERE id=? AND email=?", (entry_id, user)
        ).rowcount
    if not deleted:
        raise HTTPException(404, "Terme inconnu")
    return {"deleted": True}

@app.post("/api/dictionary/learn")
def dictionary_learn(body: DictionaryLearnRequest, user: str = Depends(current_user)):
    heard = clean_required(body.heard, "heard", 200)
    corrected = clean_required(body.corrected, "corrected", 500)
    with db() as conn:
        conn.execute(
            "INSERT INTO personal_dictionary(email,term,replacement,source) VALUES (?,?,?,'learned') "
            "ON CONFLICT(email,term) DO UPDATE SET replacement=excluded.replacement,source='learned'",
            (user, heard, corrected),
        )
    return {"learned": True}

@app.get("/api/dictionary/export", response_class=PlainTextResponse)
def dictionary_export(user: str = Depends(current_user)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["term", "replacement"])
    with db() as conn:
        rows = conn.execute(
            "SELECT term,replacement FROM personal_dictionary WHERE email=? ORDER BY term",
            (user,),
        ).fetchall()
    writer.writerows((row["term"], row["replacement"]) for row in rows)
    return output.getvalue()

@app.post("/api/dictionary/import")
async def dictionary_import(file: UploadFile, user: str = Depends(current_user)):
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    imported = 0
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or not {"term", "replacement"}.issubset(reader.fieldnames):
        raise HTTPException(400, "CSV attendu: term,replacement")
    with db() as conn:
        for row in reader:
            term = (row.get("term") or "").strip()[:200]
            replacement = (row.get("replacement") or "").strip()[:500]
            if not term or not replacement:
                continue
            conn.execute(
                "INSERT INTO personal_dictionary(email,term,replacement,source) VALUES (?,?,?,'personal') "
                "ON CONFLICT(email,term) DO UPDATE SET replacement=excluded.replacement",
                (user, term, replacement),
            )
            imported += 1
    return {"imported": imported}

@app.post("/api/dictionary/analyze")
async def dictionary_analyze(file: UploadFile, user: str = Depends(current_user)):
    text = (await file.read()).decode("utf-8", errors="replace")[:200_000]
    candidates = set(re.findall(r"\b(?:[A-Z]{2,8}|[A-Z][a-z]+(?:[- ][A-Z][a-z]+)+)\b", text))
    added = 0
    with db() as conn:
        for term in sorted(candidates)[:100]:
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO personal_dictionary(email,term,replacement,source) "
                "VALUES (?,?,?,'learned')", (user, term, term),
            )
            added += conn.total_changes - before
    return {"terms_added": added}

@app.post("/api/snippets")
def snippet_add(body: SnippetRequest, user: str = Depends(current_user)):
    trigger = clean_required(body.trigger, "trigger", 200)
    content = clean_required(body.content, "content", 4000)
    with db() as conn:
        conn.execute(
            "INSERT INTO snippets(email,trigger,content) VALUES (?,?,?) "
            "ON CONFLICT(email,trigger) DO UPDATE SET content=excluded.content",
            (user, trigger, content),
        )
        row = conn.execute(
            "SELECT id FROM snippets WHERE email=? AND trigger=?", (user, trigger)
        ).fetchone()
    return {"id": row["id"]}

@app.delete("/api/snippets/{snippet_id}")
def snippet_delete(snippet_id: int, user: str = Depends(current_user)):
    with db() as conn:
        deleted = conn.execute(
            "DELETE FROM snippets WHERE id=? AND email=?", (snippet_id, user)
        ).rowcount
    if not deleted:
        raise HTTPException(404, "Snippet inconnu")
    return {"deleted": True}

@app.get("/api/formatting-rules")
def formatting_rules(user: str = Depends(current_user)):
    with db() as conn:
        shared = conn.execute(
            "SELECT id,rule FROM formatting_rules WHERE shared=1 ORDER BY id"
        ).fetchall()
        personal = conn.execute(
            "SELECT id,rule FROM formatting_rules WHERE shared=0 AND email=? ORDER BY id",
            (user,),
        ).fetchall()
    return {"shared": [dict(row) for row in shared], "personal": [dict(row) for row in personal]}

@app.post("/api/formatting-rules")
def formatting_rule_add(body: FormattingRuleRequest, user: str = Depends(current_user)):
    rule = clean_required(body.rule, "rule", 1000)
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO formatting_rules(email,rule,shared) VALUES (?,?,0)", (user, rule)
        )
    return {"id": cursor.lastrowid}

@app.delete("/api/formatting-rules/{rule_id}")
def formatting_rule_delete(rule_id: int, user: str = Depends(current_user)):
    with db() as conn:
        deleted = conn.execute(
            "DELETE FROM formatting_rules WHERE id=? AND email=? AND shared=0",
            (rule_id, user),
        ).rowcount
    if not deleted:
        raise HTTPException(404, "Regle inconnue")
    return {"deleted": True}

@app.get("/api/ai-skills")
def ai_skills(_: str = Depends(current_user)):
    return {"skills": AI_SKILLS}

# --- transcription (proxy vers faster-whisper-server)
@app.post("/api/transcribe")
async def transcribe(file: UploadFile, user: str = Depends(current_user)):
    audio = await file.read()
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{WHISPER_URL}/v1/audio/transcriptions",
            files={"file": (file.filename or "audio.wav", audio)},
            data={"model": "large-v3-turbo"},
        )
    if r.status_code != 200:
        raise HTTPException(502, "Moteur de transcription indisponible")
    count_usage(user, "transcription")
    return {"text": r.json().get("text", "")}

async def llm_complete(system_prompt: str, text: str) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{LLM_URL}/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
            },
        )
    if r.status_code != 200:
        raise HTTPException(502, "Moteur de reformulation indisponible")
    return r.json()["choices"][0]["message"]["content"]

# --- reformulation (proxy vers LLM, API OpenAI-compatible)
@app.post("/api/reformulate")
async def reformulate(body: ReformulateRequest, user: str = Depends(current_user)):
    context, snippets = writing_context(user)
    source_text = apply_snippets(body.text, snippets)
    system_prompt = body.style_prompt
    if context:
        system_prompt = f"{system_prompt}\n\n{context}"
    result = await llm_complete(system_prompt, source_text)
    count_usage(user, "reformulation")
    return {"text": result}

@app.post("/api/command")
async def command(body: CommandRequest, user: str = Depends(current_user)):
    instruction = clean_required(body.instruction, "instruction", 2000)
    system_prompt = command_prompt(instruction, vocabulary_context(user))
    return {"text": await llm_complete(system_prompt, body.text)}

@app.post("/api/engineering-notes")
async def engineering_notes(body: CommandRequest, user: str = Depends(current_user)):
    context, snippets = writing_context(user)
    source_text = apply_snippets(body.text, snippets)
    prompt = (
        "Transform the raw engineering dictation into concise sections: Observation, "
        "Hypothesis, Method, Result, Action. Omit empty sections, preserve measurements "
        "and uncertainty, and never invent facts."
    )
    if body.instruction.strip():
        prompt += f"\nAdditional instruction: {body.instruction.strip()}"
    if context:
        prompt += f"\n\n{context}"
    return {"text": await llm_complete(prompt, source_text)}

# ================================================================ ADMIN
@app.get("/api/admin/overview")
def admin_overview(_: bool = Depends(require_admin)):
    with db() as conn:
        users = conn.execute("""
            SELECT u.email, u.role, u.cohort, u.note, u.disabled, u.created_at, u.last_seen,
                   (SELECT COUNT(*) FROM tokens t WHERE t.email=u.email AND t.active=1) AS machines,
                   COALESCE((SELECT SUM(g.transcriptions) FROM usage g WHERE g.email=u.email),0) AS total_transcriptions,
                   COALESCE((SELECT SUM(g.reformulations) FROM usage g WHERE g.email=u.email),0) AS total_reformulations
            FROM users u ORDER BY u.last_seen DESC
        """).fetchall()
        today = time.strftime("%Y-%m-%d")
        day_stats = conn.execute("""
            SELECT COALESCE(SUM(transcriptions),0) t, COALESCE(SUM(reformulations),0) r,
                   COUNT(DISTINCT email) actifs
            FROM usage WHERE day=?
        """, (today,)).fetchone()
        seats = conn.execute("SELECT COUNT(*) c FROM tokens WHERE active=1").fetchone()["c"]
    return {
        "seats_used": seats, "max_seats": MAX_SEATS, "domains": ALLOWED_DOMAINS,
        "today": dict(day_stats),
        "users": [dict(u) for u in users],
    }

@app.get("/api/admin/user/{email}")
def admin_user_detail(email: str, _: bool = Depends(require_admin)):
    """Profil complet : infos + appareils + historique d'usage (30 jours, compteurs)."""
    email = email.lower()
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u:
            raise HTTPException(404, "Utilisateur inconnu")
        machines = conn.execute(
            "SELECT machine, active, created_at FROM tokens WHERE email=?", (email,)
        ).fetchall()
        history = conn.execute(
            "SELECT day, transcriptions, reformulations, audio_seconds FROM usage "
            "WHERE email=? ORDER BY day DESC LIMIT 30", (email,)
        ).fetchall()
    return {"profile": dict(u),
            "machines": [dict(m) for m in machines],
            "usage_30d": [dict(h) for h in history]}

@app.post("/api/admin/user/{email}")
def admin_user_update(email: str, body: UserUpdate, _: bool = Depends(require_admin)):
    """L'admin a tous les droits : rôle, cohorte, note, suspension."""
    email = email.lower()
    fields, vals = [], []
    if body.role is not None:
        if body.role not in ("student", "teacher", "staff", "partner"):
            raise HTTPException(400, "Rôle invalide")
        fields.append("role=?"); vals.append(body.role)
    if body.cohort is not None:
        fields.append("cohort=?"); vals.append(body.cohort)
    if body.note is not None:
        fields.append("note=?"); vals.append(body.note)
    if body.disabled is not None:
        fields.append("disabled=?"); vals.append(1 if body.disabled else 0)
    if not fields:
        return {"updated": False}
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO users(email, created_at) VALUES (?,?)",
                     (email, time.time()))
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE email=?", (*vals, email))
    return {"updated": True}

@app.post("/api/admin/user/{email}/revoke-machines")
def admin_revoke_machines(email: str, _: bool = Depends(require_admin)):
    """Déconnecte l'utilisateur de tous ses appareils (il devra refaire un code email)."""
    with db() as conn:
        n = conn.execute("UPDATE tokens SET active=0 WHERE email=?",
                         (email.lower(),)).rowcount
    return {"revoked_tokens": n}

@app.delete("/api/admin/user/{email}")
def admin_user_delete(email: str, _: bool = Depends(require_admin)):
    """Supprime complètement l'utilisateur (profil, tokens, compteurs)."""
    email = email.lower()
    with db() as conn:
        conn.execute("DELETE FROM tokens WHERE email=?", (email,))
        conn.execute("DELETE FROM users WHERE email=?", (email,))
        conn.execute("DELETE FROM usage WHERE email=?", (email,))
        conn.execute("DELETE FROM auth_codes WHERE email=?", (email,))
    return {"deleted": email}

@app.post("/api/admin/cohort/{cohort}/disable")
def admin_cohort_disable(cohort: str, _: bool = Depends(require_admin)):
    """Suspend toute une cohorte (ex. fin d'année scolaire)."""
    with db() as conn:
        n = conn.execute("UPDATE users SET disabled=1 WHERE cohort=?", (cohort,)).rowcount
    return {"disabled_users": n}

# ---------------------------------------------------------------- admin UI
@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return (Path(__file__).parent / "admin.html").read_text(encoding="utf-8")
