"""
Nova Server — MVP + panneau d'administration
Serveur de transcription + reformulation on-premise pour établissements.
Auth : email académique + code à 6 chiffres.
Confidentialité : seuls des COMPTEURS d'usage sont stockés, jamais le contenu.
"""
import os
import random
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr

# ---------------------------------------------------------------- config
DB_PATH = Path(os.getenv("NOVA_DB", "nova.db"))
ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("NOVA_DOMAINS", "eleves.ipsa.fr,ipsa.fr").split(",")]
MAX_SEATS = int(os.getenv("NOVA_MAX_SEATS", "300"))
MAX_MACHINES_PER_EMAIL = int(os.getenv("NOVA_MAX_MACHINES", "3"))
CODE_TTL_SECONDS = 600          # code email valide 10 min
CODE_MAX_ATTEMPTS = 5
REQUIRE_AUTH = os.getenv("NOVA_REQUIRE_AUTH", "true").lower() == "true"

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
            active INTEGER DEFAULT 1, created_at REAL
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
        """)
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
            """SELECT t.email, u.disabled FROM tokens t
               LEFT JOIN users u ON u.email = t.email
               WHERE t.token=? AND t.active=1""", (token,)
        ).fetchone()
    if not row:
        raise HTTPException(401, "Token invalide ou révoqué")
    if row["disabled"]:
        raise HTTPException(403, "Compte suspendu — contactez votre administrateur")
    touch_user(row["email"])
    return row["email"]

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

# ---------------------------------------------------------------- app
app = FastAPI(title="Nova Server", version="0.2.0")

@app.get("/api/health")
def health():
    return {"status": "ok", "domains": ALLOWED_DOMAINS}

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

        seats = conn.execute("SELECT COUNT(*) c FROM tokens WHERE active=1").fetchone()["c"]
        if seats >= MAX_SEATS:
            raise HTTPException(403, "Nombre maximal d'utilisateurs atteint")
        machines = conn.execute(
            "SELECT COUNT(*) c FROM tokens WHERE email=? AND active=1", (email,)
        ).fetchone()["c"]
        if machines >= MAX_MACHINES_PER_EMAIL:
            raise HTTPException(403, "Nombre maximal d'appareils atteint")

        token = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM auth_codes WHERE email=?", (email,))
        conn.execute("INSERT INTO tokens VALUES (?,?,?,1,?)",
                     (token, email, body.machine, time.time()))
    touch_user(email)
    return {"token": token}

# --- profil de l'utilisateur connecté (pour l'app)
@app.get("/api/me")
def me(user: str = Depends(current_user)):
    with db() as conn:
        u = conn.execute("SELECT email, role, cohort FROM users WHERE email=?", (user,)).fetchone()
    return dict(u) if u else {"email": user}

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

# --- reformulation (proxy vers LLM, API OpenAI-compatible)
@app.post("/api/reformulate")
async def reformulate(body: ReformulateRequest, user: str = Depends(current_user)):
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{LLM_URL}/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": body.style_prompt},
                    {"role": "user", "content": body.text},
                ],
                "temperature": 0.3,
            },
        )
    if r.status_code != 200:
        raise HTTPException(502, "Moteur de reformulation indisponible")
    count_usage(user, "reformulation")
    return {"text": r.json()["choices"][0]["message"]["content"]}

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
