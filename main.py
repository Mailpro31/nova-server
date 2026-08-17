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
import base64
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
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
# Nature de l'organisation. `business` est accepté par le modèle mais aucun
# client ne l'expose encore.
ORGANIZATION_TYPE = os.getenv("NOVA_ORGANIZATION_TYPE", "education").lower()
if ORGANIZATION_TYPE not in {"education", "business"}:
    ORGANIZATION_TYPE = "education"
EDUCATION_MODE = os.getenv("NOVA_EDUCATION_MODE", "normal").lower()
AI_SKILLS_REQUIRED = os.getenv("NOVA_AI_SKILLS_REQUIRED", "false").lower() == "true"
AI_SKILLS_TRACK_PROGRESS = os.getenv("NOVA_AI_SKILLS_TRACK_PROGRESS", "true").lower() == "true"

# Microsoft Entra ID utilise le flux OAuth 2.0 Device Code : adapté à une app
# desktop, sans secret client embarqué. La méthode n'est annoncée que lorsque
# l'identifiant d'application est configuré côté serveur.
ENTRA_TENANT_ID = os.getenv("NOVA_ENTRA_TENANT_ID", "organizations")
ENTRA_CLIENT_ID = os.getenv("NOVA_ENTRA_CLIENT_ID", "")
ENTRA_SCOPES = os.getenv("NOVA_ENTRA_SCOPES", "openid profile email User.Read")
# Tenant Microsoft autorisé à s'authentifier, quand l'établissement le connaît.
# Vide = comportement historique inchangé (seul le domaine e-mail filtre).
# Renseigné = le `tid` du jeton d'identité doit correspondre : c'est le mapping
# explicite « tenant externe → organisation Nova », qui ne repose pas sur le
# suffixe d'adresse.
ENTRA_ALLOWED_TENANT_ID = os.getenv("NOVA_ENTRA_ALLOWED_TENANT_ID", "").strip()
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

# Environnement d'exécution. Le défaut est `production` : un déploiement qui
# oublie de le déclarer obtient le comportement le plus prudent, jamais le plus
# bavard. Seul `development` autorise l'affichage d'un code de connexion.
NOVA_ENV = os.getenv("NOVA_ENV", "production").strip().lower()

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
        -- Organisations connues du serveur. Mono-organisation aujourd'hui,
        -- mais la table porte déjà une clé propre : le jour où un Control
        -- Plane en gérera plusieurs, rien n'est à réinventer.
        -- État propre à cette instance de serveur. Sert notamment à retenir
        -- l'organisation qu'elle sert, indépendamment du slug configuré.
        CREATE TABLE IF NOT EXISTS server_state(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS organizations(
            id TEXT PRIMARY KEY,               -- UUID Nova, immuable
            slug TEXT,                         -- NOVA_ORGANIZATION_ID, lisible
            display_name TEXT NOT NULL,        -- « IPSA Paris » : un libellé
            org_type TEXT NOT NULL DEFAULT 'education',
            entra_tenant_id TEXT,              -- mapping explicite, jamais déduit
            google_customer_id TEXT,
            oidc_issuer TEXT,
            created_at REAL
        );
        -- Identités fédérées. Une identité n'est pas une permission : cette
        -- table dit QUI se connecte, jamais ce qu'il a le droit de faire.
        CREATE TABLE IF NOT EXISTS federated_identities(
            provider TEXT NOT NULL,            -- microsoft_entra | google_workspace | oidc
            external_subject TEXT NOT NULL,    -- oid/sub : immuable, jamais l'e-mail
            external_tenant_id TEXT,           -- tid Microsoft, customer Google, issuer OIDC
            organization_id TEXT NOT NULL,
            email TEXT NOT NULL,               -- lien vers users, la clé actuelle
            -- Niveau de garantie de l'identité :
            --   transport_only : revendications lues d'un jeton reçu en TLS
            --                    serveur-à-serveur, signature NON vérifiée
            --   verified       : signature, émetteur, audience et expiration
            --                    vérifiés (aucun flux ne produit ce niveau)
            verification TEXT NOT NULL DEFAULT 'transport_only',
            created_at REAL,
            last_seen REAL,
            PRIMARY KEY (provider, external_subject)
        );
        """)
        migrate_schema(conn)

def migrate_schema(conn):
    """Migrations additives uniquement : aucune colonne n'est supprimée, aucun
    compte existant n'est perdu, et une base d'avant cette phase reste lisible
    par l'ancien code (les nouvelles colonnes sont simplement ignorées)."""
    def columns(table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    token_columns = columns("tokens")
    if "expires_at" not in token_columns:
        conn.execute("ALTER TABLE tokens ADD COLUMN expires_at REAL")
    if "token_hash" not in token_columns:
        conn.execute("ALTER TABLE tokens ADD COLUMN token_hash TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash)"
    )
    migrate_tokens_to_hashes(conn)

    if "verification" not in columns("federated_identities"):
        conn.execute(
            "ALTER TABLE federated_identities ADD COLUMN verification "
            "TEXT NOT NULL DEFAULT 'transport_only'"
        )

    user_columns = columns("users")
    additive_user_columns = {
        # Identifiant interne stable. L'adresse e-mail reste la clé primaire
        # historique — la changer casserait toutes les tables qui la référencent.
        "user_id": "TEXT",
        "organization_id": "TEXT",
        # active | disabled | deprovisioned. `disabled` (0/1) est conservé et
        # reste la colonne que lisent l'admin et `current_user`.
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "identity_provider": "TEXT NOT NULL DEFAULT 'legacy_email_code'",
        "external_subject": "TEXT",
        "external_tenant_id": "TEXT",
    }
    for column, definition in additive_user_columns.items():
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    organization_id = bootstrap_organization(conn)
    # Rattrapage des comptes créés avant cette phase : chacun reçoit un
    # identifiant interne et l'organisation du serveur.
    for row in conn.execute(
        "SELECT email FROM users WHERE user_id IS NULL OR user_id=''"
    ).fetchall():
        conn.execute(
            "UPDATE users SET user_id=? WHERE email=?", (str(uuid.uuid4()), row["email"])
        )
    conn.execute(
        "UPDATE users SET organization_id=? WHERE organization_id IS NULL OR organization_id=''",
        (organization_id,),
    )
    # `status` suit `disabled` pour les comptes déjà suspendus.
    conn.execute("UPDATE users SET status='disabled' WHERE disabled=1 AND status='active'")


ORGANIZATION_ID_STATE_KEY = "organization_id"


def seed_organization_id() -> str:
    """Graine d'identifiant, utilisée **une seule fois**, au tout premier
    démarrage d'une instance.

    Dérivée du slug plutôt que tirée au hasard pour une raison de migration :
    deux serveurs déjà déployés pour le même établissement (bascule, reprise
    après incident, restauration d'une sauvegarde antérieure à cette phase)
    convergent vers le même identifiant au lieu d'en inventer deux. Passé ce
    premier démarrage, la valeur vient de la base et cette fonction n'est plus
    consultée.

    Le libellé n'entre pas dans le calcul, et le slug n'y entre qu'une fois :
    voir `nova_organization_id`.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://nova.id/organization/{ORGANIZATION_ID}"))


def read_server_state(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM server_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def nova_organization_id() -> str:
    """Identifiant **immuable** de l'organisation servie par cette instance.

    Lu en base, jamais recalculé. C'est la propriété fondamentale du modèle :

    - renommer le libellé (`NOVA_ORGANIZATION_NAME`) ne le change pas ;
    - **renommer le slug (`NOVA_ORGANIZATION_ID`) ne le change pas non plus** ;
    - un redémarrage retrouve la même valeur ;
    - aucune adresse e-mail n'entre dans son calcul.

    Le slug reste un identifiant humain — lisible, utilisable pour du routage,
    modifiable. L'identifiant de tenant, lui, est attribué une fois et pour
    toutes.

    À terme, c'est le **Nova Control Plane** qui attribuera cet UUID et le
    fournira au serveur de l'organisation ; `seed_organization_id()` ne sert que
    d'amorçage de compatibilité pour les instances déjà déployées.
    """
    with db() as conn:
        stored = read_server_state(conn, ORGANIZATION_ID_STATE_KEY)
    return stored or seed_organization_id()


def bootstrap_organization(conn) -> str:
    """Attribue l'identifiant de l'organisation **au premier démarrage**, puis
    se contente de rafraîchir ce qui est modifiable.

    Le rattachement instance → organisation passe par `server_state`, pas par le
    slug : sans cela, renommer le slug ferait échouer la recherche et créerait
    silencieusement une seconde organisation pour le même établissement.
    """
    organization_id = read_server_state(conn, ORGANIZATION_ID_STATE_KEY)

    if organization_id is None:
        organization_id = seed_organization_id()
        conn.execute(
            "INSERT OR IGNORE INTO organizations(id, slug, display_name, org_type, "
            "entra_tenant_id, created_at) VALUES (?,?,?,?,?,?)",
            (
                organization_id,
                ORGANIZATION_ID,
                ORGANIZATION_NAME,
                ORGANIZATION_TYPE,
                ENTRA_ALLOWED_TENANT_ID or None,
                time.time(),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO server_state(key, value) VALUES (?,?)",
            (ORGANIZATION_ID_STATE_KEY, organization_id),
        )

    # Slug, libellé, type et mapping de tenant sont modifiables. L'identifiant
    # de la ligne, non : la clause `WHERE id=?` ne bouge jamais.
    conn.execute(
        "UPDATE organizations SET slug=?, display_name=?, org_type=?, entra_tenant_id=? "
        "WHERE id=?",
        (
            ORGANIZATION_ID,
            ORGANIZATION_NAME,
            ORGANIZATION_TYPE,
            ENTRA_ALLOWED_TENANT_ID or None,
            organization_id,
        ),
    )
    return organization_id


# ---------------------------------------------------------------- jetons
#
# Un jeton de session est un secret **porteur** : quiconque le détient est
# l'utilisateur. Le stocker en clair signifie qu'un accès en lecture à la base
# — sauvegarde égarée, injection SQL, poste d'administration compromis — livre
# des sessions immédiatement utilisables.
#
# Le serveur ne conserve donc que l'empreinte. Un SHA-256 simple suffit ici, et
# un dérivateur lent (bcrypt, argon2) serait un contresens : ces fonctions
# protègent des secrets à **faible** entropie contre la force brute. Un jeton
# `secrets.token_urlsafe(32)` porte 256 bits d'aléa — il n'y a rien à deviner —
# et le ralentissement se paierait sur chaque requête authentifiée.

TOKEN_HASH_MARKER = "h:"


def hash_token(token: str) -> str:
    """Empreinte d'un jeton. Ne journalise jamais son entrée ni sa sortie."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def migrate_tokens_to_hashes(conn):
    """Remplace les jetons stockés en clair par leur empreinte, **sans perdre
    une seule session**.

    C'est possible parce que la base contient encore le jeton en clair : on peut
    donc calculer son empreinte tout de suite. Aucun régime transitoire n'est
    nécessaire, et surtout aucune ambiguïté « ce champ contient-il un jeton ou
    une empreinte ? » ne subsiste après la migration.

    La colonne `token` est conservée — rien n'est supprimé — mais ne contient
    plus de secret : elle reçoit `h:<empreinte>`, valeur unique qui préserve la
    clé primaire tout en étant inutilisable pour s'authentifier.
    """
    pending = conn.execute(
        "SELECT token FROM tokens WHERE token_hash IS NULL AND token IS NOT NULL"
    ).fetchall()
    for row in pending:
        stored = row["token"]
        if not stored or stored.startswith(TOKEN_HASH_MARKER):
            continue
        digest = hash_token(stored)
        conn.execute(
            "UPDATE tokens SET token_hash=?, token=? WHERE token=?",
            (digest, f"{TOKEN_HASH_MARKER}{digest}", stored),
        )


init_db()

def touch_user(email: str):
    """Crée le profil s'il n'existe pas + met à jour last_seen."""
    # Résolu avant d'ouvrir la connexion : `nova_organization_id()` lit la base,
    # et l'imbriquer dans une transaction en écriture exposerait à un verrou.
    organization_id = nova_organization_id()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(email, created_at, user_id, organization_id) "
            "VALUES (?,?,?,?)",
            (email, time.time(), str(uuid.uuid4()), organization_id),
        )
        conn.execute("UPDATE users SET last_seen=? WHERE email=?", (time.time(), email))


# ---------------------------------------------------------------- identité
#
# Frontière de confiance : tout ce qui suit est décidé **par le serveur**. Le
# Desktop ne peut ni proposer ni influencer un `security_role`, un
# `organization_id` ou un `member_type` — il ne fait que lire ce que cette
# section produit.

# `users.role` décrit un métier. La correspondance ne produit jamais de droit.
MEMBER_TYPE_BY_ROLE = {
    "student": "student",
    "teacher": "teacher",
    "staff": "staff",
    # `partner` est la catégorie fourre-tout du serveur : elle ne désigne
    # aucun métier.
    "partner": "other",
}


def member_type_for(role: str) -> str | None:
    """Nature métier du membre, `None` si le rôle stocké n'en désigne aucune."""
    return MEMBER_TYPE_BY_ROLE.get((role or "").strip().lower())


def security_role_for(_row) -> str:
    """Rôle de sécurité Nova.

    Toujours `member`. Aucun métier, aucun groupe, aucun domaine d'adresse et
    aucun champ envoyé par le client ne peut l'élever. Le panneau
    d'administration s'authentifie par `X-Admin-Token`, hors du modèle
    utilisateur : il ne fait de personne un `organization_admin`. Le jour où ce
    rôle existera, il sera une colonne écrite par un administrateur, lue ici.
    """
    return "member"


def groups_for(row) -> list[dict]:
    """Groupes du membre. La cohorte historique devient un groupe de
    compatibilité — même notion, source explicite pour qu'un futur groupe
    d'annuaire ne puisse pas s'y confondre."""
    cohort = (row["cohort"] or "").strip() if "cohort" in row.keys() else ""
    if not cohort:
        return []
    return [
        {
            "id": cohort,
            "label": cohort,
            "source": "legacy_cohort",
            "external_group_id": None,
        }
    ]


def account_status(row) -> str:
    """active | disabled | deprovisioned, en restant compatible avec la colonne
    booléenne `disabled` que lit encore le reste du serveur."""
    if "disabled" in row.keys() and row["disabled"]:
        return "disabled"
    status = (row["status"] or "active") if "status" in row.keys() else "active"
    return status if status in {"active", "disabled", "deprovisioned"} else "active"


def record_federated_identity(
    email: str,
    provider: str,
    external_subject: str,
    external_tenant_id: str | None,
    verification: str = "transport_only",
):
    """Associe une identité externe **immuable** à un compte.

    `external_subject` est l'`oid`/`sub` du fournisseur, jamais l'adresse : une
    adresse change, se réattribue, et n'existe pas dans certains annuaires. La
    clé primaire `(provider, external_subject)` garantit qu'un même sujet chez
    deux fournisseurs reste deux identités distinctes.

    `verification` dit ce qu'on sait réellement de la provenance. Le flux Device
    Code actuel ne produit que `transport_only` : les revendications viennent
    d'un jeton reçu en TLS depuis Microsoft, mais sa signature n'est pas
    vérifiée. **L'identité enregistrée n'est donc pas autoritative** — elle est
    conservée pour préparer la migration, jamais consultée pour autoriser. Ce
    qui autorise reste le domaine de l'adresse et, s'il est configuré, le
    mapping de tenant.
    """
    if not external_subject:
        return
    now = time.time()
    organization_id = nova_organization_id()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO federated_identities(provider, external_subject, "
            "external_tenant_id, organization_id, email, verification, created_at, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                provider,
                external_subject,
                external_tenant_id,
                organization_id,
                email,
                verification,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE federated_identities SET last_seen=?, email=?, external_tenant_id=?, "
            "verification=? WHERE provider=? AND external_subject=?",
            (now, email, external_tenant_id, verification, provider, external_subject),
        )
        conn.execute(
            "UPDATE users SET identity_provider=?, external_subject=?, external_tenant_id=? "
            "WHERE email=?",
            (provider, external_subject, external_tenant_id, email),
        )


def decode_jwt_claims(token: str) -> dict:
    """Lit la charge utile d'un JWT **sans vérifier sa signature**.

    Légitime ici, et seulement ici : le jeton est reçu directement de
    `login.microsoftonline.com` par un appel serveur-à-serveur sur TLS, il n'est
    à aucun moment fourni par le client. Il ne doit **jamais** être employé sur
    un jeton reçu du Desktop ou d'un navigateur — dans ce cas, signature,
    émetteur, audience et expiration doivent être vérifiés.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}

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
        # Un code à usage unique imprimé dans les journaux est un secret en
        # clair, consultable par quiconque lit `docker logs`. L'afficher exige
        # donc un mode développement **explicitement** demandé : une production
        # dont le SMTP est mal configuré échoue bruyamment plutôt que de
        # divulguer silencieusement les codes de tous ses utilisateurs.
        if NOVA_ENV == "development":
            print(f"[DEV] Code pour {to} : {code}")
            return
        raise RuntimeError(
            "SMTP non configure : impossible d'envoyer le code. "
            "Renseignez NOVA_SMTP_HOST, ou NOVA_ENV=development en developpement."
        )
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
    # La base ne contient que des empreintes : on compare des empreintes.
    token_hash = hash_token(token)
    with db() as conn:
        row = conn.execute(
            """SELECT t.email, t.expires_at, u.disabled, u.status FROM tokens t
               LEFT JOIN users u ON u.email = t.email
               WHERE t.token_hash=? AND t.active=1""", (token_hash,)
        ).fetchone()
    if not row:
        raise HTTPException(401, "Token invalide ou révoqué")
    if row["disabled"] or (row["status"] or "active") != "active":
        raise HTTPException(403, "Compte suspendu — contactez votre administrateur")
    if row["expires_at"] and row["expires_at"] < time.time():
        with db() as conn:
            conn.execute("UPDATE tokens SET active=0 WHERE token_hash=?", (token_hash,))
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
        # Seule l'empreinte est écrite. Le jeton lui-même ne quitte cette
        # fonction que vers le client, et n'est jamais journalisé.
        digest = hash_token(token)
        expires_at = now + TOKEN_TTL_SECONDS
        conn.execute(
            "INSERT INTO tokens(token,token_hash,email,machine,active,created_at,expires_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"{TOKEN_HASH_MARKER}{digest}", digest, email, machine, 1, now, expires_at),
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
        # Identifiant Nova immuable de l'organisation. Distinct du slug lisible
        # (`organization.id`) et du nom d'affichage : c'est celui qui servira de
        # clé de tenant le jour du Control Plane.
        "organization_id": nova_organization_id(),
        "organization_type": ORGANIZATION_TYPE,
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
            "UPDATE tokens SET active=0 WHERE token_hash=? AND email=?",
            (hash_token(token), user),
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

    # Identité Microsoft immuable. `oid` est l'identifiant de l'objet
    # utilisateur dans le tenant : il survit à un changement d'adresse, là où
    # `sub` n'est stable que par couple (application, utilisateur).
    claims = decode_jwt_claims(token_payload.get("id_token", ""))
    external_tenant_id = claims.get("tid")
    external_subject = claims.get("oid") or claims.get("sub") or ""

    # Mapping explicite « tenant externe → organisation Nova ». Quand
    # l'établissement a déclaré son tenant, le suffixe d'adresse ne suffit plus
    # à entrer : c'est le tenant qui décide. Non configuré, le comportement
    # historique est conservé à l'identique.
    if ENTRA_ALLOWED_TENANT_ID and external_tenant_id != ENTRA_ALLOWED_TENANT_ID:
        ENTRA_FLOWS.pop(body.flow_id, None)
        raise HTTPException(403, "Compte Microsoft hors etablissement")

    ENTRA_FLOWS.pop(body.flow_id, None)
    session = issue_token(email, flow["machine"])
    record_federated_identity(email, "microsoft_entra", external_subject, external_tenant_id)
    return {"status": "complete", "email": email, **session}

# --- profil de l'utilisateur connecté (pour l'app)
@app.get("/api/me")
def me(user: str = Depends(current_user)):
    """Profil du membre connecté.

    **Strictement additif.** Les trois champs historiques `email`, `role` et
    `cohort` gardent leur nom, leur type et leur place à la racine ; le client
    actuel les désérialise sans voir le reste. Le champ `organization` reste
    une **chaîne** — le nom d'affichage — parce que le Desktop déployé le
    déclare déjà ainsi : en faire un objet casserait chaque poste installé.
    Les données structurées vivent donc dans des champs nouveaux, et la forme
    du contrat est annoncée par `contract_version`.
    """
    organization_id = nova_organization_id()
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE email=?", (user,)).fetchone()
        organization = conn.execute(
            "SELECT id, display_name, org_type FROM organizations WHERE id=?",
            (organization_id,),
        ).fetchone()
    if not u:
        return {"email": user}

    capabilities = public_campus_config()["capabilities"]
    return {
        # — contrat historique, inchangé —
        "email": u["email"],
        "role": u["role"],
        "cohort": u["cohort"],
        "organization": organization["display_name"] if organization else ORGANIZATION_NAME,
        # — contrat v2, additif —
        "contract_version": 2,
        "user_id": u["user_id"],
        "organization_id": u["organization_id"],
        "organization_type": organization["org_type"] if organization else ORGANIZATION_TYPE,
        "membership": {
            "member_type": member_type_for(u["role"]),
            "security_role": security_role_for(u),
            "groups": groups_for(u),
            "status": account_status(u),
        },
        "identity": {
            "provider": u["identity_provider"] or "legacy_email_code",
            # Le sujet externe n'est pas renvoyé : le client n'en a aucun usage
            # et le diffuser élargirait la surface pour rien.
            "has_external_identity": bool(u["external_subject"]),
        },
        "capabilities": sorted(name for name, enabled in capabilities.items() if enabled),
    }

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
