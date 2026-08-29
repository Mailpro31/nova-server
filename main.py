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
import ipaddress
import json
import secrets
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from urllib.parse import urlencode, urlparse

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field

import connector_release
import database
import deployment
import migrations
import observability
import production_config
import ratelimit
import scim
import secrets_provider
from ai_runtime import (
    AI_MODEL_NOT_READY,
    AI_RUNTIME_BUSY,
    AI_RUNTIME_CONFIG_INVALID,
    AI_RUNTIME_FAILED,
    AI_RUNTIME_STARTING,
    AI_RUNTIME_TIMEOUT,
    AI_RUNTIME_UNAVAILABLE,
    AIRuntimeError,
    ModelProfile,
    ReasoningMode,
    TaskType,
    detect_hardware,
    recommend_profile,
    router_from_environment,
)

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

# --- Google Workspace : même flux, contraintes propres --------------------
#
# Google émet un secret pour ses clients « Desktop » et l'exige à l'échange du
# code, tout en documentant qu'une application installée « ne peut pas garder de
# secret ». Ce n'est donc pas un secret au sens cryptographique : c'est un
# identifiant public que le protocole réclame. Il reste malgré tout côté
# serveur — jamais dans Nova.exe — parce que c'est le serveur qui échange le
# code. Le poste demeure un client public au sens strict.
GOOGLE_CLIENT_ID = os.getenv("NOVA_GOOGLE_CLIENT_ID", "").strip()
# Les secrets acceptent desormais une **reference** autant qu'une valeur :
# `env://NOM` ou `file:///run/secrets/nom`. Une valeur brute reste acceptee
# pour ne pas casser les deploiements existants, mais elle devient un choix
# visible plutot qu'un defaut — et un orchestrateur qui monte ses secrets en
# fichiers n'a plus a les recopier dans l'environnement.
GOOGLE_CLIENT_SECRET = secrets_provider.resolve_value(
    os.getenv("NOVA_GOOGLE_CLIENT_SECRET", "")
) or ""
# Domaine hébergé Workspace autorisé — le mapping explicite « organisation
# Google → organisation Nova ». Vide, le fournisseur reste indisponible : aucun
# compte Google, personnel ou non, n'entre par défaut.
GOOGLE_ALLOWED_HOSTED_DOMAIN = os.getenv("NOVA_GOOGLE_ALLOWED_HOSTED_DOMAIN", "").strip()
# Identité seule. Ni Admin SDK, ni Directory, ni Gmail, ni Drive, ni Calendar :
# la synchronisation de groupes viendra plus tard et avec ses propres portées.
GOOGLE_SCOPES = os.getenv("NOVA_GOOGLE_SCOPES", "openid email profile")

# --- OIDC générique : Okta, Keycloak, Auth0, Ping, Zitadel, authentik… -----
#
# Un seul adaptateur configurable, pas une implémentation par marque. Les points
# de terminaison ne sont pas saisis à la main : ils viennent du document de
# découverte de l'émetteur. Les seules valeurs demandées à l'exploitant sont
# celles que la découverte ne peut pas deviner.
OIDC_ISSUER = os.getenv("NOVA_OIDC_ISSUER", "").strip().rstrip("/")
OIDC_CLIENT_ID = os.getenv("NOVA_OIDC_CLIENT_ID", "").strip()
# Présent seulement si l'IdP l'exige. Il ne quitte jamais le serveur : ni vers
# le poste, ni vers /api/config, ni vers /api/me, ni vers les journaux.
OIDC_CLIENT_SECRET = secrets_provider.resolve_value(
    os.getenv("NOVA_OIDC_CLIENT_SECRET", "")
) or ""
# `none` | `client_secret_post` | `client_secret_basic`. Rien d'exotique.
OIDC_CLIENT_AUTH_METHOD = os.getenv("NOVA_OIDC_CLIENT_AUTH_METHOD", "none").strip()
OIDC_SCOPES = os.getenv("NOVA_OIDC_SCOPES", "openid email profile")
# Libellé du bouton. **Affichage uniquement** : il n'entre dans aucune décision
# de sécurité. « Continue with OIDC » ne dit rien à un utilisateur.
OIDC_DISPLAY_NAME = os.getenv("NOVA_OIDC_DISPLAY_NAME", "").strip() or "Company SSO"
# Un IdP auto-hébergé peut légitimement vivre sur un réseau privé. C'est un
# choix d'exploitation, jamais une décision du poste : cette variable n'est
# lisible que par le serveur, et rien dans l'API ne permet de la basculer.
OIDC_ALLOW_PRIVATE_ISSUER = (
    os.getenv("NOVA_OIDC_ALLOW_PRIVATE_ISSUER", "false").strip().lower() == "true"
)
# Contrainte facultative : une revendication supplémentaire à comparer
# exactement. Ni moteur de policy, ni expression — une égalité, ou rien.
OIDC_REQUIRED_CLAIM = os.getenv("NOVA_OIDC_REQUIRED_CLAIM", "").strip()
OIDC_REQUIRED_CLAIM_VALUE = os.getenv("NOVA_OIDC_REQUIRED_CLAIM_VALUE", "").strip()

# --- SSO Organization moderne : Authorization Code + PKCE ------------------
#
# Autorité `organizations` : comptes professionnels et scolaires uniquement,
# jamais de compte Microsoft personnel — Nova Organization n'a pas de sens pour
# un compte personnel. Le point d'entrée reste multi-tenant parce que Nova doit
# pouvoir servir plusieurs établissements ; c'est le `tid` du jeton vérifié,
# rapproché de la table `organizations`, qui décide de l'accès. Le point
# d'entrée ne fait entrer personne : le mapping, si.
ENTRA_PKCE_AUTHORITY = "https://login.microsoftonline.com/organizations/oauth2/v2.0"
# `openid profile email` suffit à l'identité : `oid`, `tid` et l'adresse sont
# dans le jeton d'identité. Aucun appel Graph, donc aucun consentement
# administrateur à demander pour se connecter.
ENTRA_PKCE_SCOPES = os.getenv("NOVA_ENTRA_PKCE_SCOPES", "openid profile email")
ENTRA_PKCE_FLOW_TTL_SECONDS = 600
# Tolérance d'horloge pour `exp` / `nbf`, en secondes.
ENTRA_CLOCK_SKEW_SECONDS = 120
ENTRA_AUTHORITY = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/oauth2/v2.0"
ENTRA_FLOWS: dict[str, dict] = {}

# Envoi d'emails : par défaut, mode DEV (le code est affiché dans les logs serveur)
SMTP_HOST = os.getenv("NOVA_SMTP_HOST", "")          # ex. smtp.ipsa.fr
SMTP_PORT = int(os.getenv("NOVA_SMTP_PORT", "587"))
SMTP_USER = os.getenv("NOVA_SMTP_USER", "")
SMTP_PASS = secrets_provider.resolve_value(os.getenv("NOVA_SMTP_PASS", "")) or ""
SMTP_FROM = os.getenv("NOVA_SMTP_FROM", "nova@ipsa.fr")

# Moteurs d'inférence (services du docker-compose)
ORGANIZATION_AI_ROUTER = router_from_environment()

# Environnement d'exécution. Le défaut est `production` : un déploiement qui
# oublie de le déclarer obtient le comportement le plus prudent, jamais le plus
# bavard. Seul `development` autorise l'affichage d'un code de connexion.
NOVA_ENV = os.getenv("NOVA_ENV", "production").strip().lower()

# Mode de serveur. `dedicated` sert une organisation — c'est le mode déployé
# aujourd'hui. `control_plane` en servira plusieurs : il n'existe pas encore
# complètement, mais la distinction doit exister **avant** que du code suppose
# partout qu'une seule organisation existe.
SERVER_MODE_DEDICATED = "dedicated"
SERVER_MODE_CONTROL_PLANE = "control_plane"
NOVA_SERVER_MODE = os.getenv("NOVA_SERVER_MODE", SERVER_MODE_DEDICATED).strip().lower()
if NOVA_SERVER_MODE not in {SERVER_MODE_DEDICATED, SERVER_MODE_CONTROL_PLANE}:
    NOVA_SERVER_MODE = SERVER_MODE_DEDICATED

# ── Nova Admin (console web) ─────────────────────────────────────────────
#
# La console est servie depuis une autre origine que l'API : il faut donc
# déclarer CORS. **Une liste explicite, jamais un joker.** `*` combiné à des
# identifiants est refusé par les navigateurs, et l'accepterait-on qu'il
# laisserait n'importe quelle page appeler l'administration au nom de son
# visiteur.
#
# Vide par défaut : un déploiement qui n'a pas de console web n'ouvre rien.
NOVA_ADMIN_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("NOVA_ADMIN_ORIGINS", "").split(",")
    if origin.strip()
]

# Version du contrat du Control Plane. La console doit pouvoir constater qu'elle
# parle à un serveur qu'elle ne comprend pas, plutôt que d'échouer champ par
# champ.
CONTROL_CONTRACT_VERSION = 1

# ---------------------------------------------------------------- db
#
# Point d'acces unique a la base. Le Control Plane n'ouvre plus SQLite
# directement : `database.Database` choisit le moteur selon
# `NOVA_DATABASE_URL`, traduit le SQL ecrit en dialecte SQLite quand la cible
# est PostgreSQL, et unifie les erreurs d'integrite.
#
# L'objet est cree paresseusement : l'import de ce module ne doit ouvrir
# aucune connexion, et surtout n'appliquer aucune migration.
_DATABASE: database.Database | None = None
_DATABASE_URL: str | None = None
_DATABASE_LOCK = threading.Lock()


def database_url() -> str:
    """L'URL effective de ce processus.

    `NOVA_DATABASE_URL` fait autorite. A defaut, `DB_PATH` designe un fichier
    SQLite : c'est la variable historique, et les tests la remplacent pour
    passer d'une base a l'autre. La garder vivante evite de reecrire des
    dizaines de tests pour un changement qui ne les concerne pas.
    """
    explicit = os.getenv("NOVA_DATABASE_URL", "").strip()
    if explicit:
        return explicit
    return "sqlite:///" + str(DB_PATH).replace("\\", "/")


class ProductionConfigurationError(RuntimeError):
    """Configuration refusee au demarrage. Code stable, aucun detail d'hote."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def check_production_database() -> None:
    """Refuse un plan de controle de production adosse a SQLite.

    SQLite n'a qu'un ecrivain. Un plan de controle centralise finit toujours
    par avoir plusieurs workers, souvent sur plusieurs machines : la limite ne
    se manifeste alors pas par une lenteur, mais par des ecritures refusees au
    moment de la charge. Mieux vaut le dire au demarrage.

    `NOVA_DATABASE_URL` devient obligatoire en production : se rabattre sur
    `NOVA_DB` y ferait passer un oubli de configuration pour un choix.

    Appelee au demarrage du service, jamais a l'import — comme tout le reste
    depuis cette phase.
    """
    if NOVA_ENV != "production":
        return
    if not os.getenv("NOVA_DATABASE_URL", "").strip():
        raise ProductionConfigurationError(
            "DATABASE_URL_REQUIRED_IN_PRODUCTION",
            "NOVA_DATABASE_URL must be set explicitly in production.",
        )
    if database.parse_url(database_url()).dialect != database.POSTGRESQL:
        raise ProductionConfigurationError(
            "SQLITE_REFUSED_IN_PRODUCTION",
            "A production control plane requires PostgreSQL.",
        )


def database_handle() -> database.Database:
    """La base du processus. Ouverte au premier usage, jamais a l'import.

    Le handle est indexe sur l'URL resolue : changer `DB_PATH` en ouvre un
    autre, ce qui est exactement ce qu'attend un test qui bascule d'instance.
    """
    global _DATABASE, _DATABASE_URL
    url = database_url()
    if _DATABASE is None or _DATABASE_URL != url:
        with _DATABASE_LOCK:
            if _DATABASE is None or _DATABASE_URL != url:
                if _DATABASE is not None:
                    _DATABASE.close()
                _DATABASE = database.Database(url)
                _DATABASE_URL = url
    return _DATABASE


def reset_database_handle() -> None:
    """Referme la base du processus. Destine aux tests et aux recettes."""
    global _DATABASE, _DATABASE_URL
    with _DATABASE_LOCK:
        if _DATABASE is not None:
            _DATABASE.close()
        _DATABASE = None
        _DATABASE_URL = None


@contextmanager
def db():
    # Contrat inchange : succes -> commit, exception -> rollback. Une frontiere
    # transactionnelle qui change silencieusement ferait echouer des routes
    # entieres de facon invisible.
    with database_handle().connect() as conn:
        yield conn

def init_db():
    """Prepare le schema. **Explicite** : plus jamais appelee a l'import.

    Deux chemins, et un seul point de verite. Sur une base SQLite historique,
    `migrate_schema` apporte d'abord les colonnes que l'adoption exige — sans
    elles la base serait refusee comme non reconnue. Ensuite, et dans tous les
    cas, c'est `migrations` qui fait autorite sur le schema.

    Conservee parce que les tests et les recettes l'appellent ; ce n'est plus
    une seconde definition du schema.
    """
    handle = database_handle()
    sqlite = handle.dialect == database.SQLITE
    legacy = False
    if sqlite:
        with db() as conn:
            # Une base SQLite historique doit recevoir ses colonnes manquantes
            # AVANT l'adoption : sans elles, `migrations` la refuserait comme
            # non reconnue, ce qui serait exact mais inutile.
            legacy = conn.table_exists("users")
            if legacy:
                # Combler d'abord les tables absentes : `migrate_schema` ecrit
                # dans `organizations`, qu'une base d'avant cette table n'a pas.
                # Les index viennent ensuite : une base historique porte une
                # table `organizations` d'avant `discovery_slug`, et indexer
                # une colonne que `migrate_schema` n'a pas encore ajoutee
                # echouerait.
                migrations.create_missing_tables(conn)
                migrate_schema(conn)
                migrations.create_missing_indexes(conn)

    migrations.migrate(handle)

    if sqlite and not legacy:
        # Base neuve : les tables viennent d'etre creees, `migrate_schema` n'a
        # donc pas encore pu s'executer. Il reste a lui faire poser ce qui
        # n'est pas du schema — le rattrapage des comptes historiques.
        with db() as conn:
            migrate_schema(conn)

    # L'amorcage de l'organisation n'est pas du schema : c'est la premiere
    # ligne de donnees d'une instance, et elle vaut pour les deux moteurs.
    # Elle vivait au milieu de `migrate_schema`, donc une base PostgreSQL
    # neuve n'aurait eu aucune organisation.
    with db() as conn:
        bootstrap_organization(conn)


# L'ancien `init_db` construisait ici tout le schema, en parallele de
# `migrate_schema`. Il a ete supprime : `migrations/0001_baseline.sql` est
# desormais la seule definition du schema, et deux definitions qui divergent
# valent moins qu'une seule qui fait autorite.


def normalize_email(value: str) -> str:
    """Forme canonique d'une adresse, pour la recherche et l'unicité.

    **Espaces retirés, casse abaissée. Rien d'autre.**

    La tentation serait d'aller plus loin : ignorer les points de Gmail, couper
    au `+alias`, appliquer les règles de tel ou tel fournisseur. Ce serait une
    faute. Ces règles sont propres à un opérateur, changent sans préavis, et ne
    valent pas pour un domaine d'entreprise : `prenom.nom@exemple.fr` et
    `prenomnom@exemple.fr` y désignent deux personnes différentes. Normaliser
    au-delà du sûr reviendrait à fusionner des comptes distincts.

    `lower()` plutôt que `casefold()` : ce dernier applique des replis
    linguistiques (`ß` → `ss`) qui, sur une adresse, changent le destinataire.
    """
    return (value or "").strip().lower()


class UserIdentityCollision(RuntimeError):
    """Deux comptes se disputent la même adresse dans la même organisation.

    La migration s'arrête. Choisir un gagnant reviendrait à décider, à la place
    de l'exploitant, quel compte perd son historique — et personne ne s'en
    apercevrait avant longtemps.
    """


def _email_fingerprint(value: str) -> str:
    """Empreinte courte pour un diagnostic de migration.

    Un message d'erreur qui listerait des adresses en clair les recopierait dans
    les journaux d'exploitation. L'empreinte suffit à retrouver la ligne en base.
    """
    return hashlib.sha256(normalize_email(value).encode()).hexdigest()[:12]


def migrate_users_to_tenant_scoped_identity(conn, organization_id: str):
    """Fait de `user_id` la clé primaire, et de l'e-mail un attribut de tenant.

    ## Ce que corrige cette migration

    `users.email` était la clé primaire **globale**. Deux organisations ne
    pouvaient donc pas compter chacune une `alex@example.com` — alors que ce
    sont deux personnes, sans le moindre rapport. Tant qu'une instance servait
    une organisation, le défaut restait invisible ; il rendait simplement tout
    Control Plane multi-tenant impossible.

    ## Propriétés

    - **idempotente** : au second démarrage, elle constate et sort ;
    - **fail-closed** : toute collision arrête la migration, rien n'est écrit ;
    - **sans perte** : chaque ligne est recopiée, aucun `user_id` existant n'est
      régénéré, aucune session n'est invalidée.
    """
    columns = set(conn.columns("users"))
    if not columns:
        return
    if "normalized_email" in columns:
        # Déjà migrée. On maintient seulement l'invariant sur d'éventuelles
        # lignes écrites par du code plus ancien.
        conn.execute(
            "UPDATE users SET normalized_email=lower(trim(email)) "
            "WHERE normalized_email IS NULL OR normalized_email=''"
        )
        return

    rows = [dict(row) for row in conn.execute("SELECT * FROM users").fetchall()]

    # ── Contrôles préalables, avant toute écriture ──────────────────────
    seen_keys: dict[tuple[str, str], str] = {}
    seen_user_ids: set[str] = set()
    for row in rows:
        email = (row.get("email") or "").strip()
        if not email:
            raise UserIdentityCollision(
                "migration impossible : un compte sans adresse e-mail existe "
                f"(user_id={row.get('user_id') or 'absent'})"
            )
        org = (row.get("organization_id") or "").strip() or organization_id
        key = (org, normalize_email(email))
        if key in seen_keys:
            raise UserIdentityCollision(
                "migration impossible : deux comptes partagent la même adresse "
                f"dans la même organisation (empreinte={_email_fingerprint(email)}). "
                "Fusionnez ou renommez l'un des deux avant de redémarrer."
            )
        seen_keys[key] = email
        user_id = (row.get("user_id") or "").strip()
        if user_id:
            if user_id in seen_user_ids:
                raise UserIdentityCollision(
                    f"migration impossible : user_id dupliqué ({user_id})"
                )
            seen_user_ids.add(user_id)

    # ── Reconstruction ─────────────────────────────────────────────────
    #
    # SQLite ne sait pas changer une clé primaire : il faut reconstruire. La
    # table de correspondance e-mail → user_id est calculée d'abord, car les
    # tables qui référencent les comptes en ont besoin après la bascule.
    now = time.time()
    mapping: dict[str, str] = {}
    prepared = []
    for row in rows:
        email = (row.get("email") or "").strip()
        # Un `user_id` existant n'est **jamais** régénéré : c'est l'identité
        # d'une personne, y compris de celle validée en Phase 16.
        user_id = (row.get("user_id") or "").strip() or str(uuid.uuid4())
        mapping[normalize_email(email)] = user_id
        prepared.append((
            user_id,
            (row.get("organization_id") or "").strip() or organization_id,
            email,
            normalize_email(email),
            row.get("role") or "student",
            row.get("security_role") or SECURITY_ROLE_MEMBER,
            row.get("cohort") or "",
            row.get("note") or "",
            row.get("disabled") or 0,
            row.get("status") or "active",
            row.get("identity_provider"),
            row.get("external_subject"),
            row.get("external_tenant_id"),
            row.get("created_at") or now,
            now,
            row.get("last_seen"),
        ))

    conn.execute("DROP TABLE IF EXISTS users_migrated")
    conn.execute("""
        CREATE TABLE users_migrated(
            user_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            email TEXT NOT NULL,
            normalized_email TEXT NOT NULL,
            role TEXT DEFAULT 'student',
            security_role TEXT NOT NULL DEFAULT 'member',
            cohort TEXT DEFAULT '',
            note TEXT DEFAULT '',
            disabled INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            identity_provider TEXT,
            external_subject TEXT,
            external_tenant_id TEXT,
            created_at REAL,
            updated_at REAL,
            last_seen REAL
        )
    """)
    conn.executemany(
        "INSERT INTO users_migrated VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", prepared
    )

    migrated = conn.execute("SELECT COUNT(*) c FROM users_migrated").fetchone()["c"]
    if migrated != len(rows):
        # Ceinture et bretelles : la contrainte d'unicité aurait déjà levé.
        raise UserIdentityCollision(
            f"migration incohérente : {len(rows)} comptes lus, {migrated} écrits"
        )

    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_migrated RENAME TO users")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_org_email "
        "ON users(organization_id, normalized_email)"
    )
    link_user_references(conn, mapping, organization_id)
    # ASCII strictement : la console Windows par défaut est en cp1252, et une
    # flèche « → » y fait lever `UnicodeEncodeError` — au milieu d'une
    # migration, donc au pire moment possible.
    print(f"[migration] users to user_id accounts={len(rows)}")


def backfill_user_references(conn, organization_id: str):
    """Rattache à un compte toute ligne restée sans `user_id`.

    Appelée à chaque démarrage, et idempotente : elle ne touche que les lignes
    dont le `user_id` est absent. Sans elle, une session héritée — écrite avant
    que les jetons portent une identité — deviendrait inutilisable, puisque le
    contrôle d'authentification joint désormais par `user_id` et non par
    adresse. Une session valide ne doit pas se perdre dans une migration.
    """
    tables = ("auth_codes", "tokens", "usage", "personal_dictionary",
              "snippets", "formatting_rules", "federated_identities")
    for table in tables:
        existing = set(conn.columns(table))
        if "email" not in existing:
            continue
        # Les colonnes sont garanties ici, et pas seulement lors de la
        # migration : une base créée de zéro doit avoir exactement la même forme
        # qu'une base migrée, sans quoi les deux divergeraient en silence.
        if "user_id" not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
            existing.add("user_id")
        if "organization_id" not in existing and table != "federated_identities":
            conn.execute(f"ALTER TABLE {table} ADD COLUMN organization_id TEXT")
            existing.add("organization_id")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table}(user_id)"
        )
        conn.execute(
            f"UPDATE {table} SET user_id=("
            f"  SELECT u.user_id FROM users u"
            f"  WHERE u.normalized_email=lower(trim({table}.email))"
            f"    AND u.organization_id=?"
            f") WHERE user_id IS NULL OR user_id=''",
            (organization_id,),
        )
        if "organization_id" in existing and table != "federated_identities":
            conn.execute(
                f"UPDATE {table} SET organization_id=? "
                f"WHERE organization_id IS NULL OR organization_id=''",
                (organization_id,),
            )


def link_user_references(conn, mapping: dict[str, str], organization_id: str):
    """Rattache les tables liées à `user_id` plutôt qu'à une adresse.

    L'adresse reste présente là où elle sert de repère lisible, mais elle cesse
    d'être le lien d'intégrité : une personne qui change d'adresse ne doit pas
    perdre ses sessions, ses appareils ni son dictionnaire.

    Les lignes orphelines — un e-mail sans compte — gardent un `user_id` vide
    plutôt que d'être supprimées : détruire des données pendant une migration
    est le meilleur moyen de perdre ce qu'on ne comprenait pas encore.
    """
    linked = {
        "auth_codes": "email",
        "tokens": "email",
        "usage": "email",
        "personal_dictionary": "email",
        "snippets": "email",
        "formatting_rules": "email",
        "federated_identities": "email",
    }
    for table, email_column in linked.items():
        existing = set(conn.columns(table))
        if not existing:
            continue
        if "user_id" not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
        if "organization_id" not in existing and table != "federated_identities":
            conn.execute(f"ALTER TABLE {table} ADD COLUMN organization_id TEXT")
        for normalized, user_id in mapping.items():
            conn.execute(
                f"UPDATE {table} SET user_id=? WHERE lower(trim({email_column}))=? "
                f"AND (user_id IS NULL OR user_id='')",
                (user_id, normalized),
            )
        if table != "federated_identities":
            conn.execute(
                f"UPDATE {table} SET organization_id=? "
                f"WHERE organization_id IS NULL OR organization_id=''",
                (organization_id,),
            )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table}(user_id)"
        )


def migrate_schema(conn):
    """Migrations additives uniquement : aucune colonne n'est supprimée, aucun
    compte existant n'est perdu, et une base d'avant cette phase reste lisible
    par l'ancien code (les nouvelles colonnes sont simplement ignorées)."""
    # Mise a niveau d'une base **SQLite historique** : reconstruction de tables
    # et introspection `PRAGMA`, deux choses qui n'existent que la. Une base
    # PostgreSQL n'a pas d'historique a rattraper — elle nait au dernier schema
    # par `migrations` — et laisser ce code s'y executer donnerait une erreur
    # obscure au lieu d'un refus clair.
    if getattr(conn, "dialect", database.SQLITE) != database.SQLITE:
        raise database.DatabaseError(
            "LEGACY_MIGRATION_SQLITE_ONLY",
            "migrate_schema upgrades historical SQLite databases only.",
        )

    def columns(table: str) -> set[str]:
        return set(conn.columns(table))

    token_columns = columns("tokens")
    if "expires_at" not in token_columns:
        conn.execute("ALTER TABLE tokens ADD COLUMN expires_at REAL")
    if "token_hash" not in token_columns:
        conn.execute("ALTER TABLE tokens ADD COLUMN token_hash TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash)"
    )
    migrate_tokens_to_hashes(conn)

    connector_columns = columns("connectors")
    if connector_columns:
        for column, definition in {
            "credential_generation": "INTEGER NOT NULL DEFAULT 1",
            "credential_created_at": "REAL",
            "pending_credential_hash": "TEXT",
            "pending_created_at": "REAL",
            "rotation_state": "TEXT NOT NULL DEFAULT 'stable'",
            "rotation_requested_at": "REAL",
            "last_rotated_at": "REAL",
            "platform_os": "TEXT",
            "platform_arch": "TEXT",
            "distribution_mode": "TEXT",
            "update_state": "TEXT NOT NULL DEFAULT 'up_to_date'",
            "update_policy": "TEXT NOT NULL DEFAULT 'manual'",
            "target_version": "TEXT",
            "last_known_good_version": "TEXT",
            "last_update_at": "REAL",
            "last_update_error": "TEXT",
        }.items():
            if column not in connector_columns:
                conn.execute(f"ALTER TABLE connectors ADD COLUMN {column} {definition}")

    runtime_columns = columns("organization_ai_runtime_config")
    if runtime_columns:
        for column, definition in {
            # La chaine complete de la Phase 33 : souhaite -> livre -> applique.
            "delivered_revision": "INTEGER NOT NULL DEFAULT 0",
            "delivered_at": "REAL",
            "applied_at": "REAL",
            "last_error_code": "TEXT",
        }.items():
            if column not in runtime_columns:
                conn.execute(
                    f"ALTER TABLE organization_ai_runtime_config "
                    f"ADD COLUMN {column} {definition}"
                )

    if "google_hosted_domain" not in columns("organizations"):
        conn.execute("ALTER TABLE organizations ADD COLUMN google_hosted_domain TEXT")

    organization_columns = columns("organizations")
    for column, definition in {
        "discovery_slug": "TEXT",
        "discovery_enabled": "INTEGER NOT NULL DEFAULT 0",
        "deployment_mode": "TEXT NOT NULL DEFAULT 'dedicated'",
        "service_endpoint": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'active'",
    }.items():
        if column not in organization_columns:
            conn.execute(f"ALTER TABLE organizations ADD COLUMN {column} {definition}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_slug "
        "ON organizations(discovery_slug)"
    )

    # Rôle de sécurité Nova. Une colonne écrite par un opérateur, lue par le
    # serveur — jamais déduite d'un métier, d'un domaine d'adresse ou d'une
    # revendication d'IdP. Le défaut vaut `member` : une base existante ne
    # fabrique aucun administrateur en migrant.
    if "security_role" not in columns("users"):
        conn.execute(
            "ALTER TABLE users ADD COLUMN security_role TEXT NOT NULL DEFAULT 'member'"
        )

    # But de la tentative : les tentatives déjà en base sont des connexions
    # ordinaires, et le défaut le dit. Aucune ne devient rétroactivement une
    # preuve d'administration.
    flow_columns = columns("sso_flows")
    if "purpose" not in flow_columns:
        conn.execute(
            "ALTER TABLE sso_flows ADD COLUMN purpose TEXT NOT NULL DEFAULT 'login'"
        )
    if "expected_user_id" not in flow_columns:
        conn.execute("ALTER TABLE sso_flows ADD COLUMN expected_user_id TEXT")

    if "verification" not in columns("federated_identities"):
        conn.execute(
            "ALTER TABLE federated_identities ADD COLUMN verification "
            "TEXT NOT NULL DEFAULT 'transport_only'"
        )

    if "external_issuer" not in columns("federated_identities"):
        # SQLite ne sait pas modifier une clé primaire : la table est
        # reconstruite, puis **toutes** les lignes sont recopiées. Les identités
        # Microsoft et Google existantes reçoivent un émetteur vide, ce qui
        # laisse leur clé inchangée en pratique. Idempotent : la condition
        # ci-dessus ne se vérifie qu'une fois.
        conn.execute("""
            CREATE TABLE federated_identities_next(
                provider TEXT NOT NULL,
                external_subject TEXT NOT NULL,
                external_tenant_id TEXT,
                organization_id TEXT NOT NULL,
                email TEXT NOT NULL,
                verification TEXT NOT NULL DEFAULT 'transport_only',
                external_issuer TEXT NOT NULL DEFAULT '',
                created_at REAL,
                last_seen REAL,
                PRIMARY KEY (organization_id, provider, external_issuer, external_subject)
            )
        """)
        conn.execute("""
            INSERT INTO federated_identities_next(
                provider, external_subject, external_tenant_id, organization_id,
                email, verification, external_issuer, created_at, last_seen)
            SELECT provider, external_subject, external_tenant_id, organization_id,
                   email, verification, '', created_at, last_seen
            FROM federated_identities
        """)
        conn.execute("DROP TABLE federated_identities")
        conn.execute(
            "ALTER TABLE federated_identities_next RENAME TO federated_identities"
        )

    # A provider subject is scoped by the Nova organization. Entra `oid`, for
    # example, is stable inside one tenant, not globally across a Control Plane.
    # Rebuild the historical key once, preserving every row and stable user_id.
    identity_info = conn.execute(
        "PRAGMA table_info(federated_identities)"
    ).fetchall()
    identity_pk = [
        row["name"]
        for row in sorted(identity_info, key=lambda item: item["pk"])
        if row["pk"]
    ]
    expected_identity_pk = [
        "organization_id", "provider", "external_issuer", "external_subject"
    ]
    if identity_pk != expected_identity_pk:
        identity_columns = {row["name"] for row in identity_info}
        conn.execute("DROP TABLE IF EXISTS federated_identities_tenant_scoped")
        conn.execute("""
            CREATE TABLE federated_identities_tenant_scoped(
                user_id TEXT,
                provider TEXT NOT NULL,
                external_subject TEXT NOT NULL,
                external_tenant_id TEXT,
                organization_id TEXT NOT NULL,
                email TEXT NOT NULL,
                verification TEXT NOT NULL DEFAULT 'transport_only',
                external_issuer TEXT NOT NULL DEFAULT '',
                created_at REAL,
                last_seen REAL,
                PRIMARY KEY (
                    organization_id, provider, external_issuer, external_subject
                )
            )
        """)
        user_id_select = "user_id" if "user_id" in identity_columns else "NULL"
        conn.execute(f"""
            INSERT INTO federated_identities_tenant_scoped(
                user_id, provider, external_subject, external_tenant_id,
                organization_id, email, verification, external_issuer,
                created_at, last_seen)
            SELECT {user_id_select}, provider, external_subject,
                   external_tenant_id, organization_id, email, verification,
                   external_issuer, created_at, last_seen
            FROM federated_identities
        """)
        conn.execute("DROP TABLE federated_identities")
        conn.execute(
            "ALTER TABLE federated_identities_tenant_scoped "
            "RENAME TO federated_identities"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_federated_identities_user "
            "ON federated_identities(user_id)"
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

    migrate_users_to_tenant_scoped_identity(conn, organization_id)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_org_email "
        "ON users(organization_id, normalized_email)"
    )
    backfill_user_references(conn, organization_id)

    bootstrap_provider_configs(conn, organization_id)
    bootstrap_discovery(conn, organization_id)

    # Tentatives d'authentification périmées, laissées par une exécution
    # précédente : elles n'ont plus aucune utilité et ne doivent pas
    # s'accumuler. Un simple ménage au démarrage suffit ; le reste se fait à la
    # création de chaque nouvelle tentative.
    conn.execute(
        "DELETE FROM sso_flows WHERE expires_at < ?",
        (time.time() - SSO_FLOW_RETENTION_SECONDS,),
    )


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
        # `INSERT OR REPLACE` n'existe pas sous PostgreSQL, et son equivalent
        # exact est l'upsert sur la cle : `ON CONFLICT ... DO UPDATE`, que les
        # deux moteurs comprennent de la meme facon.
        conn.execute(
            "INSERT INTO server_state(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (ORGANIZATION_ID_STATE_KEY, organization_id),
        )

    # Slug, libellé, type et mapping de tenant sont modifiables. L'identifiant
    # de la ligne, non : la clause `WHERE id=?` ne bouge jamais.
    conn.execute(
        "UPDATE organizations SET slug=?, display_name=?, org_type=?, entra_tenant_id=?, "
        "google_hosted_domain=?, oidc_issuer=? WHERE id=?",
        (
            ORGANIZATION_ID,
            ORGANIZATION_NAME,
            ORGANIZATION_TYPE,
            ENTRA_ALLOWED_TENANT_ID or None,
            GOOGLE_ALLOWED_HOSTED_DOMAIN or None,
            OIDC_ISSUER or None,
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
    # Un jeton hérité ne porte pas encore de `user_id` : le lui donner fait
    # partie de sa migration. Le contrôle d'authentification joint désormais par
    # identité, et une session valide ne doit pas se perdre en chemin.
    user_columns = set(conn.columns("users"))
    if {"user_id", "normalized_email"} <= user_columns:
        backfill_user_references(conn, nova_organization_id())

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


# ------------------------------------- Configurations de fournisseur par organisation
#
# ## Ce qui change, et pourquoi
#
# Jusqu'ici, un fournisseur était configuré par des variables d'environnement
# globales à l'instance : une seule organisation Google, un seul émetteur OIDC.
# Cela suffisait pour un serveur d'établissement dédié, mais interdisait deux
# choses que la suite exige — plusieurs organisations sur un même plan de
# contrôle, et plusieurs IdP OIDC pour une même organisation.
#
# La configuration vit donc désormais en base, rattachée à une organisation, et
# porte son propre identifiant immuable.
#
# ## Global contre par-organisation
#
# La distinction n'est pas cosmétique.
#
# - **Application Microsoft** : une seule, multi-tenant, appartenant à Nova. Ce
#   qui varie d'une organisation à l'autre est le `tid` autorisé, pas
#   l'application. Dupliquer l'App Registration par client n'apporterait rien et
#   multiplierait les consentements à obtenir.
# - **Client OAuth Google** : même raisonnement. Un seul client Nova, un domaine
#   hébergé par organisation.
# - **OIDC** : là, tout est propre à l'organisation — émetteur, identifiant
#   client, secret éventuel. C'est le cas qui justifie `provider_config_id`.
#
# Autrement dit : l'identité **de Nova auprès du fournisseur** est globale ; le
# **rattachement d'une organisation** est per-organisation.

PROVIDER_TYPES = ("microsoft_entra", "google_workspace", "oidc")


def provider_config_columns(row) -> dict:
    return dict(row) if row is not None else {}


def bootstrap_provider_configs(conn, organization_id: str):
    """Reprend la configuration héritée des variables d'environnement.

    Écrit **une fois**, puis jamais réécrit : un redémarrage ne recrée pas de
    ligne, et une configuration modifiée en base n'est pas écrasée au prochain
    démarrage. C'est la différence entre importer et imposer.
    """
    now = time.time()

    def existing(provider_type: str, issuer: str | None = None):
        if issuer:
            return conn.execute(
                "SELECT id FROM organization_provider_configs "
                "WHERE organization_id=? AND provider_type=? AND oidc_issuer=?",
                (organization_id, provider_type, issuer),
            ).fetchone()
        return conn.execute(
            "SELECT id FROM organization_provider_configs "
            "WHERE organization_id=? AND provider_type=?",
            (organization_id, provider_type),
        ).fetchone()

    def insert(provider_type: str, **fields):
        columns = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "provider_type": provider_type,
            "enabled": 1,
            "created_at": now,
            "updated_at": now,
            **fields,
        }
        names = ",".join(columns)
        placeholders = ",".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO organization_provider_configs({names}) VALUES ({placeholders})",
            tuple(columns.values()),
        )

    if ENTRA_ALLOWED_TENANT_ID and not existing("microsoft_entra"):
        insert(
            "microsoft_entra",
            display_name="Microsoft",
            entra_tenant_id=ENTRA_ALLOWED_TENANT_ID,
        )

    if GOOGLE_ALLOWED_HOSTED_DOMAIN and not existing("google_workspace"):
        insert(
            "google_workspace",
            display_name="Google",
            google_hosted_domain=GOOGLE_ALLOWED_HOSTED_DOMAIN,
        )

    if OIDC_ISSUER and OIDC_CLIENT_ID and not existing("oidc", OIDC_ISSUER):
        insert(
            "oidc",
            display_name=OIDC_DISPLAY_NAME,
            oidc_issuer=OIDC_ISSUER,
            oidc_client_id=OIDC_CLIENT_ID,
            oidc_client_secret=OIDC_CLIENT_SECRET or None,
            oidc_client_auth_method=OIDC_CLIENT_AUTH_METHOD,
            oidc_allow_private_issuer=1 if OIDC_ALLOW_PRIVATE_ISSUER else 0,
            oidc_required_claim=OIDC_REQUIRED_CLAIM or None,
            oidc_required_claim_value=OIDC_REQUIRED_CLAIM_VALUE or None,
        )


def validate_provider_config(config: dict) -> str | None:
    """Motif d'invalidité, ou `None` si la configuration tient debout.

    Une configuration invalide n'est **jamais** annoncée au poste : mieux vaut
    aucun bouton qu'un bouton qui mène à une erreur incompréhensible.
    """
    provider_type = config.get("provider_type")
    if provider_type not in PROVIDER_TYPES:
        return "unknown_provider_type"

    if provider_type == "microsoft_entra":
        if not config.get("entra_tenant_id"):
            return "missing_tenant"
        if not ENTRA_CLIENT_ID:
            return "missing_global_client_id"
    elif provider_type == "google_workspace":
        if not config.get("google_hosted_domain"):
            return "missing_hosted_domain"
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            return "missing_global_client"
    elif provider_type == "oidc":
        if not config.get("oidc_issuer"):
            return "missing_issuer"
        if not config.get("oidc_client_id"):
            return "missing_client_id"
        method = config.get("oidc_client_auth_method") or "none"
        if method not in {"none", "client_secret_post", "client_secret_basic"}:
            return "unsupported_client_auth_method"
        if method != "none" and not config.get("oidc_client_secret"):
            return "missing_client_secret"
    return None


def provider_configs_for_organization(
    organization_id: str, only_usable: bool = True
) -> list[dict]:
    """Configurations d'une organisation.

    `only_usable` écarte ce qui est désactivé ou invalide — c'est ce que voit le
    poste. L'administration, elle, a besoin de tout voir, y compris le cassé.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM organization_provider_configs WHERE organization_id=? "
            "ORDER BY created_at",
            (organization_id,),
        ).fetchall()
    configs = [dict(row) for row in rows]
    if not only_usable:
        return configs
    return [
        config
        for config in configs
        if config["enabled"] and validate_provider_config(config) is None
    ]


def provider_config_by_id(config_id: str, organization_id: str) -> dict | None:
    """Configuration par identifiant, **cadrée par l'organisation**.

    L'organisation fait partie de la requête, pas d'un contrôle qui suivrait :
    un identifiant appartenant à une autre organisation ne renvoie rien, quelle
    que soit la manière dont il a été obtenu.
    """
    if not config_id:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM organization_provider_configs WHERE id=? AND organization_id=?",
            (config_id, organization_id),
        ).fetchone()
    return dict(row) if row else None


def default_provider_config(provider_type: str, organization_id: str) -> dict | None:
    """Première configuration utilisable d'un type donné.

    Chemin de compatibilité : les postes déployés désignent un fournisseur par
    son type, pas encore par son identifiant de configuration.
    """
    for config in provider_configs_for_organization(organization_id):
        if config["provider_type"] == provider_type:
            return config
    return None


# --- ce que la configuration donne au moteur -------------------------------


def config_client_id(config: dict) -> str:
    """Identifiant d'application.

    Global chez Microsoft et Google — c'est Nova qui se présente au
    fournisseur. Propre à la configuration en OIDC, où l'application appartient
    à l'organisation.
    """
    return {
        "microsoft_entra": ENTRA_CLIENT_ID,
        "google_workspace": GOOGLE_CLIENT_ID,
        "oidc": config.get("oidc_client_id") or "",
    }[config["provider_type"]]


def config_client_secret(config: dict) -> str:
    return {
        "microsoft_entra": "",
        "google_workspace": GOOGLE_CLIENT_SECRET,
        "oidc": config.get("oidc_client_secret") or "",
    }[config["provider_type"]]


def config_scopes(config: dict) -> str:
    return {
        "microsoft_entra": ENTRA_PKCE_SCOPES,
        "google_workspace": GOOGLE_SCOPES,
        "oidc": OIDC_SCOPES,
    }[config["provider_type"]]


def config_requires_secret(config: dict) -> bool:
    if config["provider_type"] == "oidc":
        return (config.get("oidc_client_auth_method") or "none") in {
            "client_secret_post",
            "client_secret_basic",
        }
    return ADAPTERS[config["provider_type"]]["uses_client_secret"]


def config_tenant_value(config: dict) -> str:
    """Valeur qui rattache une identité vérifiée à cette organisation."""
    return {
        "microsoft_entra": config.get("entra_tenant_id") or "",
        "google_workspace": config.get("google_hosted_domain") or "",
        "oidc": config.get("oidc_issuer") or "",
    }[config["provider_type"]]


def config_display_name(config: dict) -> str:
    """Libellé du bouton. **Affichage uniquement.**"""
    label = (config.get("display_name") or "").strip()
    if label:
        return label
    return {
        "microsoft_entra": "Microsoft",
        "google_workspace": "Google",
        "oidc": "Company SSO",
    }[config["provider_type"]]


def public_provider_view(config: dict) -> dict:
    """Ce que le poste a le droit de voir.

    Le strict minimum pour dessiner un bouton : un identifiant, un type, un
    libellé. Ni secret, ni identifiant client, ni tenant, ni émetteur — rien de
    tout cela n'aide à afficher un bouton, et tout cela renseignerait un
    attaquant sur l'infrastructure de l'organisation.
    """
    return {
        "id": config["id"],
        "type": config["provider_type"],
        "display_name": config_display_name(config),
    }



# ------------------------------------------------ État des tentatives SSO
#
# ## Pourquoi la mémoire du processus ne suffisait pas
#
# Une connexion SSO se déroule en deux appels séparés par un aller-retour dans
# le navigateur : `start` puis `exchange`. Tant que l'état vivait dans un
# dictionnaire du processus, ces deux appels devaient atterrir sur **le même**
# worker. Un répartiteur de charge, un redémarrage, un simple `--workers 2`
# suffisaient à casser une connexion en cours — avec un message d'expiration
# incompréhensible pour l'utilisateur, qui venait pourtant de s'authentifier.
#
# L'état vit donc en base. Le moteur d'authentification ne dépend plus du
# processus qui l'exécute.
#
# ## Ce qui est stocké, et ce qui ne l'est jamais
#
# Le `flow_id` est un secret porteur : le présenter suffit à réclamer une
# tentative. Il est donc traité comme un jeton de session — seule son empreinte
# est écrite. Même raisonnement pour le `nonce`, qui n'a jamais besoin d'être
# relu en clair : il suffit de comparer l'empreinte de celui que porte le jeton
# d'identité à celle que l'on attendait.
#
# Ne sont **jamais** persistés : code d'autorisation, `code_verifier` (il ne
# quitte pas le poste), jetons du fournisseur, jeton Nova, secret client.
#
# ## Machine à états
#
#     pending ──begin_exchange──▶ exchanging ──mark_consumed──▶ consumed
#        │                            │
#        └────────── expiration ──────┘
#
# `begin_exchange` est **atomique** : deux workers qui présentent le même
# `flow_id` au même instant ne peuvent pas passer tous les deux. Un seul obtient
# la tentative, l'autre reçoit un refus.

SSO_FLOW_STATUS_PENDING = "pending"
SSO_FLOW_STATUS_EXCHANGING = "exchanging"
SSO_FLOW_STATUS_CONSUMED = "consumed"

# À quoi sert une tentative. Une connexion ordinaire et une réauthentification
# d'administration empruntent le même moteur, mais ne sont pas interchangeables :
# un retour de connexion ne doit jamais devenir une preuve d'administration.
FLOW_PURPOSE_LOGIN = "login"
FLOW_PURPOSE_ADMIN_STEP_UP = "admin_step_up"

# Au-delà, la tentative est perdue. Assez large pour une authentification
# multifacteur, assez court pour qu'un état oublié ne traîne pas.
SSO_FLOW_TTL_SECONDS = 600
# Les tentatives terminées ou expirées sont effacées après ce délai : la table
# ne doit pas grossir indéfiniment, et ces lignes n'ont plus aucune utilité.
SSO_FLOW_RETENTION_SECONDS = 3600


class SsoFlowStore:
    """Contrat du stockage des tentatives.

    Une seule implémentation existe — la base du serveur — mais l'interface est
    explicite parce que le stockage changera : un Control Plane réparti
    voudra Postgres ou Redis, et le moteur d'authentification ne doit pas s'en
    apercevoir.
    """

    def create(self, **fields) -> str:  # pragma: no cover - contrat
        raise NotImplementedError

    def begin_exchange(self, flow_id: str) -> dict | None:  # pragma: no cover
        raise NotImplementedError

    def mark_consumed(self, flow_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def cleanup(self) -> int:  # pragma: no cover
        raise NotImplementedError


class DatabaseSsoFlowStore(SsoFlowStore):
    """Tentatives stockées dans la base du serveur.

    SQLite suffit à l'échelle d'une instance dédiée : les écritures sont rares
    (une par connexion), courtes, et la transaction implicite d'un `UPDATE`
    conditionnel donne l'atomicité recherchée. Ce ne sera pas le stockage d'un
    Control Plane réparti — voir `docs/architecture/shared-sso-flow-state.md`.
    """

    def create(
        self,
        provider_type: str,
        provider_config_id: str,
        organization_id: str,
        nonce: str,
        redirect_uri: str,
        machine: str,
        ttl_seconds: int = SSO_FLOW_TTL_SECONDS,
        purpose: str = FLOW_PURPOSE_LOGIN,
        expected_user_id: str | None = None,
    ) -> str:
        """Ouvre une tentative et renvoie son identifiant **en clair**.

        Cet identifiant ne repassera jamais par la base : seule son empreinte y
        est écrite, exactement comme pour un jeton de session.
        """
        flow_id = secrets.token_urlsafe(24)
        now = time.time()
        # Ménage opportuniste : pas de tâche de fond, pas de minuterie — le
        # nettoyage se fait au moment où l'on écrit de toute façon.
        self.cleanup()
        with db() as conn:
            conn.execute(
                "INSERT INTO sso_flows(flow_id_hash, provider_type, provider_config_id, "
                "organization_id, nonce_hash, redirect_uri, machine, purpose, "
                "expected_user_id, status, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    hash_token(flow_id),
                    provider_type,
                    provider_config_id,
                    organization_id,
                    hash_token(nonce),
                    redirect_uri,
                    machine,
                    purpose,
                    expected_user_id,
                    SSO_FLOW_STATUS_PENDING,
                    now,
                    now + ttl_seconds,
                ),
            )
        return flow_id

    def begin_exchange(self, flow_id: str) -> dict | None:
        """Réserve la tentative pour l'échange, **une seule fois**.

        Le passage `pending → exchanging` est fait par un `UPDATE` conditionnel
        et vérifié par son nombre de lignes touchées. C'est ce qui empêche deux
        workers de lancer deux échanges avec le même code d'autorisation.

        Renvoie la tentative réservée, ou `None` si elle est inconnue, expirée,
        déjà en cours d'échange ou déjà consommée.
        """
        if not flow_id:
            return None
        flow_hash = hash_token(flow_id)
        now = time.time()
        with db() as conn:
            changed = conn.execute(
                "UPDATE sso_flows SET status=?, exchanging_at=? "
                "WHERE flow_id_hash=? AND status=? AND expires_at>?",
                (SSO_FLOW_STATUS_EXCHANGING, now, flow_hash,
                 SSO_FLOW_STATUS_PENDING, now),
            ).rowcount
            if changed != 1:
                return None
            row = conn.execute(
                "SELECT * FROM sso_flows WHERE flow_id_hash=?", (flow_hash,)
            ).fetchone()
        return dict(row) if row else None

    def mark_consumed(self, flow_id: str) -> None:
        with db() as conn:
            conn.execute(
                "UPDATE sso_flows SET status=?, consumed_at=? WHERE flow_id_hash=?",
                (SSO_FLOW_STATUS_CONSUMED, time.time(), hash_token(flow_id)),
            )

    def cleanup(self) -> int:
        """Efface les tentatives terminées ou périmées depuis assez longtemps."""
        cutoff = time.time() - SSO_FLOW_RETENTION_SECONDS
        with db() as conn:
            return conn.execute(
                "DELETE FROM sso_flows WHERE expires_at < ? OR "
                "(consumed_at IS NOT NULL AND consumed_at < ?)",
                (cutoff, cutoff),
            ).rowcount


SSO_FLOW_STORE: SsoFlowStore = DatabaseSsoFlowStore()


# ------------------------------------------------- Découverte d'organisation
#
# ## Ce que résout cette brique
#
# Aujourd'hui, un poste trouve son serveur par une adresse : déposée par la DSI,
# ou saisie par l'utilisateur. Une adresse est une mauvaise chose à demander —
# elle est longue, elle change, elle expose une topologie, et elle se trompe
# silencieusement (la Phase 16 en a fait les frais avec un port erroné).
#
# La découverte remplace « où est votre serveur ? » par « quelle est votre
# organisation ? ».
#
# ## Ce que cet endpoint est, et n'est pas
#
# Il est **public** : personne n'est authentifié quand il l'appelle. Il ne donne
# donc aucun accès, et ne doit rien révéler qu'un annuaire public ne révélerait.
#
# Il n'est surtout pas un annuaire des clients de Nova. Il n'y a ni recherche,
# ni liste, ni complétion : on demande une organisation précise, et l'on obtient
# de quoi la joindre — ou une réponse générique.

DISCOVERY_CONTRACT_VERSION = 1

# Modes de déploiement. `dedicated` est le mode actuel : une instance sert une
# organisation. `control_plane` désignera une instance qui en sert plusieurs —
# il n'existe pas encore, et le résolveur ci-dessous refuse d'en faire semblant.
DEPLOYMENT_MODE_DEDICATED = "dedicated"
DEPLOYMENT_MODE_CONTROL_PLANE = "control_plane"

ORGANIZATION_STATUS_ACTIVE = "active"

# Adresse à laquelle le poste doit s'adresser ensuite. En mode dédié, c'est
# l'adresse publique du serveur de l'établissement ; en mode Control Plane, ce
# sera une passerelle Nova. Vide, la découverte reste indisponible : mieux vaut
# ne rien annoncer qu'annoncer une adresse fausse.
SERVICE_ENDPOINT = os.getenv("NOVA_SERVICE_ENDPOINT", "").strip().rstrip("/")
# Le code que l'utilisateur saisit. Ce n'est **pas** un secret : le connaître ne
# donne rien. C'est un identifiant public, comme un nom de domaine.
DISCOVERY_SLUG = os.getenv("NOVA_DISCOVERY_SLUG", "").strip()
# Hors production, un endpoint en clair sur la boucle locale est légitime.
DISCOVERY_ALLOW_INSECURE_ENDPOINT = (
    os.getenv("NOVA_DISCOVERY_ALLOW_INSECURE_ENDPOINT", "").strip().lower() == "true"
    or NOVA_ENV == "development"
)


class DiscoveryRequest(BaseModel):
    # Envoyé dans le corps plutôt que dans l'URL : un identifiant
    # d'organisation n'est pas secret, mais il n'a pas besoin de se retrouver
    # dans les journaux de chaque intermédiaire réseau.
    organization: str


def normalize_discovery_slug(value: str) -> str:
    """Forme canonique d'un identifiant d'organisation.

    Insensible à la casse et aux espaces : « IPSA », « ipsa » et « Ipsa »
    désignent la même organisation. Quelqu'un qui recopie un code depuis un
    courriel ne doit pas échouer pour une majuscule.
    """
    return value.strip().lower()


def host_is_publicly_routable(host: str, require_resolvable: bool) -> tuple[bool, str]:
    """L'hôte désigne-t-il uniquement des adresses publiquement routables ?

    Politique réseau commune à la découverte et à l'adaptateur OIDC. Les deux
    répondent à la même question — « cet hôte est-il la machine elle-même, ou son
    voisinage immédiat ? » — et deux implémentations divergentes de cette
    question finiraient par ne plus refuser les mêmes choses.

    `require_resolvable` est la seule différence entre les deux appelants, et
    elle est délibérée :

    - **OIDC** va réellement contacter l'hôte. Un nom qu'il ne résout pas est
      inutilisable, et le refuser ne coûte rien ;
    - **la découverte** ne contacte pas l'adresse : elle l'annonce à des postes
      qui, eux, peuvent la résoudre sur un réseau que ce serveur ne voit pas.
      Refuser sur une non-résolution rendrait la découverte indisponible pour
      un déploiement parfaitement sain.

    Ce que la non-résolution n'excuse pas : une IP littérale interne et un nom
    local sont refusés sans qu'aucun DNS n'ait à répondre.
    """
    name = host.strip().strip("[]").rstrip(".").lower()
    if not name:
        return False, "missing_host"
    # Refusés par leur nom : aucun DNS n'a besoin de coopérer pour cela, et un
    # résolveur menteur ne peut pas les faire passer.
    if name == "localhost" or name.endswith(".localhost"):
        return False, "internal_address"

    literal = None
    try:
        literal = ipaddress.ip_address(name)
    except ValueError:
        pass

    if literal is not None:
        addresses = [literal]
    else:
        try:
            addresses = [
                ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)
            ]
        except (socket.gaierror, ValueError, UnicodeError):
            addresses = []
        if not addresses:
            return (False, "unresolvable_host") if require_resolvable else (True, "ok")

    # Toutes les adresses doivent être acceptables : un nom qui résout à la fois
    # vers une adresse publique et vers une adresse interne servirait de pont.
    for address in addresses:
        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False, "internal_address"
    return True, "ok"


def service_endpoint_is_allowed(endpoint: str, allow_insecure: bool) -> tuple[bool, str]:
    """L'adresse annoncée est-elle acceptable ?

    Le poste va s'y connecter et lui confier une session : une réponse de
    découverte qui l'enverrait ailleurs serait une redirection d'authentification.
    D'où une validation stricte, côté serveur **et** côté poste.

    Le poste refuse déjà les adresses locales, et c'est la barrière qui compte —
    c'est lui qui détient la session. Le contrôle ici attrape une faute
    différente : une configuration d'exploitation qui annoncerait une adresse
    interne à des postes qui ne pourraient jamais la joindre, ou pire, à des
    postes du même réseau.

    `allow_insecure` est un paramètre **du serveur**. Une valeur venue de la
    découverte publique ne peut pas l'activer : elle n'est jamais lue ici.
    """
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False, "malformed"
    if parsed.scheme not in {"http", "https"}:
        return False, "scheme_not_allowed"
    if parsed.scheme == "http" and not allow_insecure:
        return False, "scheme_not_https"
    if parsed.username or parsed.password:
        return False, "credentials_in_url"
    if parsed.query or parsed.fragment:
        return False, "not_a_base_url"
    if not parsed.hostname:
        return False, "missing_host"
    if not allow_insecure:
        # Sans résolution obligatoire : voir `host_is_publicly_routable`.
        return host_is_publicly_routable(parsed.hostname, require_resolvable=False)
    return True, "ok"


class OrganizationDiscoveryResolver:
    """Contrat de résolution.

    Une seule implémentation existe — le mode dédié. L'interface est explicite
    parce qu'un Control Plane en aura une seconde, et que le contrat public ne
    doit pas changer ce jour-là.
    """

    def resolve(self, slug: str) -> dict | None:  # pragma: no cover - contrat
        raise NotImplementedError


class DedicatedOrganizationResolver(OrganizationDiscoveryResolver):
    """Une instance, une organisation.

    Le point important est ce que cette classe **refuse** de faire : même si la
    base contenait d'autres organisations — de test, d'un import, d'une erreur —
    elle ne résout que celle que l'instance sert réellement. Un serveur dédié
    qui répondrait pour ses voisines serait un multi-tenant accidentel, sans
    aucun des contrôles qui vont avec.
    """

    def resolve(self, slug: str) -> dict | None:
        wanted = normalize_discovery_slug(slug)
        if not wanted:
            return None
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM organizations WHERE id=?", (nova_organization_id(),)
            ).fetchone()
        if row is None:
            return None
        organization = dict(row)
        if normalize_discovery_slug(organization.get("discovery_slug") or "") != wanted:
            return None
        return organization


DISCOVERY_RESOLVER: OrganizationDiscoveryResolver = DedicatedOrganizationResolver()


def bootstrap_discovery(conn, organization_id: str):
    """Amorce la configuration de découverte depuis l'environnement.

    Écrit **une fois**, comme les configurations de fournisseur : un redémarrage
    ne réécrit pas, et une valeur corrigée en base n'est pas écrasée.
    """
    row = conn.execute(
        "SELECT discovery_slug, service_endpoint FROM organizations WHERE id=?",
        (organization_id,),
    ).fetchone()
    if row is None:
        return
    updates = {}
    if not row["discovery_slug"]:
        # À défaut de code dédié, le slug lisible de l'organisation fait un
        # identifiant de découverte parfaitement acceptable — il est déjà public.
        slug = DISCOVERY_SLUG or ORGANIZATION_ID
        if slug:
            updates["discovery_slug"] = normalize_discovery_slug(slug)
    if not row["service_endpoint"] and SERVICE_ENDPOINT:
        updates["service_endpoint"] = SERVICE_ENDPOINT
    if updates:
        assignments = ", ".join(f"{name}=?" for name in updates)
        conn.execute(
            f"UPDATE organizations SET {assignments} WHERE id=?",
            (*updates.values(), organization_id),
        )


def discovery_bootstrap_for(organization: dict) -> dict | None:
    """Réponse de découverte, ou `None` si l'organisation n'est pas joignable.

    Volontairement pauvre. Le poste a besoin de savoir **où aller** et **qui
    c'est** ; tout le reste — identifiant de tenant interne, tenant Microsoft,
    domaine Google, émetteur OIDC, identifiants clients — resterait sans usage
    pour lui et renseignerait quiconque interroge l'endpoint.
    """
    if not organization.get("discovery_enabled"):
        return None
    if (organization.get("status") or ORGANIZATION_STATUS_ACTIVE) != ORGANIZATION_STATUS_ACTIVE:
        return None
    endpoint = organization.get("service_endpoint") or ""
    allowed, _ = service_endpoint_is_allowed(endpoint, DISCOVERY_ALLOW_INSECURE_ENDPOINT)
    if not allowed:
        return None
    return {
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "organization": {
            # Le slug, pas l'UUID interne : le poste n'a rien à faire de
            # l'identifiant de tenant tant qu'il n'est pas authentifié, et
            # `/api/me` le lui donnera ensuite.
            "slug": organization["discovery_slug"],
            "display_name": organization["display_name"],
        },
        "deployment_mode": organization.get("deployment_mode")
        or DEPLOYMENT_MODE_DEDICATED,
        # Le poste appellera `/api/auth/providers` sur cette adresse : la liste
        # des fournisseurs garde une seule source de vérité.
        "service_endpoint": endpoint,
    }



# Aucune migration a l'import. Le schema s'applique par `python -m migrations
# migrate`, jamais parce qu'un module a ete importe : c'est exactement ce qui a
# migre une base de production en Phase 30 puis en Phase 37.
print(f"[admin] server_mode={NOVA_SERVER_MODE}")

AI_RUNTIME_CONFIG_SCHEMA_VERSION = 1


def read_ai_runtime_config(organization_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM organization_ai_runtime_config WHERE organization_id=?",
            (organization_id,),
        ).fetchone()
    if row:
        return dict(row)
    return {
        "organization_id": organization_id,
        "schema_version": AI_RUNTIME_CONFIG_SCHEMA_VERSION,
        "desired_revision": 0,
        "desired_profile": ORGANIZATION_AI_ROUTER.profile.value,
        "reasoning_allowed": int(ORGANIZATION_AI_ROUTER.reasoning_allowed),
        "max_concurrent_text": ORGANIZATION_AI_ROUTER.text_capacity.limit,
        "max_concurrent_speech": ORGANIZATION_AI_ROUTER.speech_capacity.limit,
        "max_queue": ORGANIZATION_AI_ROUTER.text_capacity.max_queue,
        "applied_revision": ORGANIZATION_AI_ROUTER.applied_revision,
        "apply_status": "local_default",
        "delivered_revision": 0,
        "delivered_at": None,
        "applied_at": None,
        "last_error_code": None,
        "updated_at": None,
        "updated_by_user_id": None,
    }


def apply_ai_runtime_config_locally(config: dict) -> None:
    ORGANIZATION_AI_ROUTER.apply_local_config(
        profile=ModelProfile(config["desired_profile"]),
        reasoning_allowed=bool(config["reasoning_allowed"]),
        max_concurrent_text=int(config["max_concurrent_text"]),
        max_concurrent_speech=int(config["max_concurrent_speech"]),
        max_queue=int(config["max_queue"]),
        revision=int(config["desired_revision"]),
    )


def apply_startup_runtime_config() -> dict:
    """Applique la configuration IA enregistree, si le deploiement en a une.

    **Appelee au demarrage du service, pas a l'import.** Ce code lisait la base
    au moment ou le module etait charge : importer `main` pour une inspection
    ouvrait donc la base configuree, et echouait des que le schema n'etait pas
    a jour. Un import ne doit ni migrer, ni lire, ni decider.
    """
    _startup_runtime_config = read_ai_runtime_config(nova_organization_id())
    if NOVA_SERVER_MODE == SERVER_MODE_DEDICATED and _startup_runtime_config["desired_revision"]:
        try:
            apply_ai_runtime_config_locally(_startup_runtime_config)
        except AIRuntimeError as error:
            # Une configuration enregistree que ce deploiement ne sait plus
            # servir — un profil `custom` dont les parametres ont disparu de
            # l'environnement, par exemple. Refuser de demarrer priverait
            # l'etablissement de sa dictee pour un reglage de confort ;
            # appliquer autre chose en silence lui mentirait. Le serveur
            # demarre sur la configuration de son environnement, et la console
            # montre un `desired` non applique — ce que la separation
            # desired/applied existe precisement pour dire.
            print(f"[ai_runtime] configuration enregistree non applicable: {error.code}")
    return _startup_runtime_config



# ────────────────────── Accès aux comptes ─────────────────────────────────
#
# Un seul endroit sait comment on retrouve, crée et modifie un compte. Le SQL
# dispersé était tenable tant que l'e-mail était une clé globale ; il ne l'est
# plus : chaque requête doit désormais porter son organisation, et un oubli
# passerait inaperçu jusqu'au jour où deux tenants partagent une base.


def user_by_id(conn, user_id: str):
    """Le compte, par son identité stable."""
    return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def user_by_email(conn, organization_id: str, email: str):
    """Le compte d'une organisation, par son adresse.

    L'organisation est un **paramètre obligatoire**, sans valeur par défaut.
    Une signature qui l'aurait rendue facultative aurait produit exactement le
    défaut que cette phase corrige.
    """
    if not organization_id:
        raise ValueError("user_by_email exige une organisation")
    return conn.execute(
        "SELECT * FROM users WHERE organization_id=? AND normalized_email=?",
        (organization_id, normalize_email(email)),
    ).fetchone()


def create_user(conn, organization_id: str, email: str, **fields):
    """Crée un compte, et lui attribue son identité une fois pour toutes."""
    now = time.time()
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users(user_id, organization_id, email, normalized_email, "
        "role, security_role, cohort, note, disabled, status, created_at, "
        "updated_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            user_id, organization_id, email.strip(), normalize_email(email),
            fields.get("role", "student"),
            fields.get("security_role", SECURITY_ROLE_MEMBER),
            fields.get("cohort", ""), fields.get("note", ""),
            1 if fields.get("disabled") else 0,
            fields.get("status", "active"), now, now, fields.get("last_seen"),
        ),
    )
    return user_id


def update_user_email(conn, user_id: str, email: str):
    """Change l'adresse d'un compte **sans toucher à son identité**.

    C'est la propriété que la clé primaire globale rendait impossible : le
    `user_id` ne bouge pas, donc les sessions, les identités fédérées, les
    appareils, le dictionnaire et le journal d'audit restent rattachés à la même
    personne. L'interface de changement d'adresse n'existe pas encore ; le
    modèle, lui, le permet.
    """
    conn.execute(
        "UPDATE users SET email=?, normalized_email=?, updated_at=? WHERE user_id=?",
        (email.strip(), normalize_email(email), time.time(), user_id),
    )


def touch_user(email: str, organization_id: str | None = None):
    """Crée le profil s'il n'existe pas, et met à jour `last_seen`.

    `INSERT OR IGNORE` ne convient plus : il s'appuyait sur l'e-mail comme clé
    primaire globale. Depuis que l'unicité est celle du couple
    (organisation, adresse), la même instruction créerait un compte de plus à
    chaque connexion.
    """
    # Résolu avant d'ouvrir la connexion : `nova_organization_id()` lit la base,
    # et l'imbriquer dans une transaction en écriture exposerait à un verrou.
    organization_id = organization_id or nova_organization_id()
    with db() as conn:
        existing = user_by_email(conn, organization_id, email)
        if existing is None:
            create_user(conn, organization_id, email, last_seen=time.time())
            return
        conn.execute(
            "UPDATE users SET last_seen=? WHERE user_id=?",
            (time.time(), existing["user_id"]),
        )


# ---------------------------------------------------------------- identité
#
# Frontière de confiance : tout ce qui suit est décidé **par le serveur**. Le
# Desktop ne peut ni proposer ni influencer un `security_role`, un
# `organization_id` ou un `member_type` — il ne fait que lire ce que cette
# section produit.

# ── Rôles de sécurité et capacités d'administration ──────────────────────
#
# Un rôle de sécurité dit ce qu'une personne a le droit de faire dans Nova. Il
# est distinct du `member_type`, qui dit son métier : un enseignant n'est pas
# administrateur parce qu'il enseigne, et un administrateur ne cesse pas de
# dicter parce qu'il administre. Confondre les deux reviendrait à faire de
# l'annuaire de l'établissement une source de privilèges.

SECURITY_ROLE_MEMBER = "member"
SECURITY_ROLE_ORGANIZATION_ADMIN = "organization_admin"
SECURITY_ROLE_IT_ADMIN = "it_admin"
SECURITY_ROLE_READ_ONLY = "read_only"

# Capacités : ce que l'on vérifie réellement dans les routes. Les rôles sont
# des noms commodes, les capacités sont la règle — sans quoi `if role ==` se
# disperse dans quarante routes et la matrice devient impossible à relire.
CAPABILITY_ORGANIZATION_READ = "organization_read"
CAPABILITY_PROVIDER_MANAGE = "provider_manage"
CAPABILITY_DISCOVERY_MANAGE = "discovery_manage"
CAPABILITY_DEPLOYMENT_MANAGE = "deployment_manage"
CAPABILITY_IDENTITY_MANAGE = "identity_manage"
CAPABILITY_SECURITY_MANAGE = "security_manage"
CAPABILITY_DIAGNOSTICS_READ = "diagnostics_read"
CAPABILITY_PROVISIONING_MANAGE = "provisioning_manage"
# Modifier les policies produit de l'organisation. Une capacite dediee, et
# non `identity_manage` ou `security_manage` detournee : gouverner ce que le
# produit offre aux membres n'est ni gerer des personnes, ni surveiller la
# securite. Reutiliser une capacite voisine ferait grossir son sens jusqu'a
# ce qu'on ne sache plus ce qu'elle protege.
CAPABILITY_POLICY_MANAGE = "policy_manage"
# Preparer et publier le contenu distribue par l'organisation. Une seule
# capacite pour les trois types : une par type ferait grossir la matrice
# sans repondre a une question differente — qui peut publier du contenu ?
CAPABILITY_CONTENT_MANAGE = "content_manage"

# La matrice. Aucun rôle n'est un sur-ensemble des autres : `organization_admin`
# gère les personnes, `it_admin` gère la machine. Les faire coïncider
# reviendrait à n'avoir qu'un seul rôle déguisé en trois.
CAPABILITIES_BY_ROLE: dict[str, frozenset[str]] = {
    SECURITY_ROLE_ORGANIZATION_ADMIN: frozenset({
        CAPABILITY_ORGANIZATION_READ,
        CAPABILITY_PROVIDER_MANAGE,
        CAPABILITY_DISCOVERY_MANAGE,
        CAPABILITY_IDENTITY_MANAGE,
        CAPABILITY_SECURITY_MANAGE,
        CAPABILITY_DIAGNOSTICS_READ,
        CAPABILITY_PROVISIONING_MANAGE,
        # Decider ce que l'organisation offre a ses membres est une decision
        # d'organisation, pas d'exploitation : `it_admin` ne la recoit pas.
        # Il gere la machine — fournisseurs, deploiement, diagnostics — et la
        # frontiere posee en Phase 22 tient sans exception ajoutee.
        CAPABILITY_POLICY_MANAGE,
        CAPABILITY_CONTENT_MANAGE,
    }),
    SECURITY_ROLE_IT_ADMIN: frozenset({
        CAPABILITY_ORGANIZATION_READ,
        CAPABILITY_PROVIDER_MANAGE,
        CAPABILITY_DISCOVERY_MANAGE,
        CAPABILITY_DEPLOYMENT_MANAGE,
        CAPABILITY_DIAGNOSTICS_READ,
        CAPABILITY_PROVISIONING_MANAGE,
    }),
    SECURITY_ROLE_READ_ONLY: frozenset({
        CAPABILITY_ORGANIZATION_READ,
        CAPABILITY_DIAGNOSTICS_READ,
    }),
    # Un membre n'a aucune capacité d'administration. La ligne est écrite
    # explicitement : l'absence de clé se lirait comme un oubli.
    SECURITY_ROLE_MEMBER: frozenset(),
}

SECURITY_ROLES = frozenset(CAPABILITIES_BY_ROLE)
ADMIN_SECURITY_ROLES = frozenset(
    role for role, caps in CAPABILITIES_BY_ROLE.items() if caps
)


def capabilities_for_role(role: str) -> frozenset[str]:
    """Ce qu'un rôle autorise. Un rôle inconnu n'autorise rien."""
    return CAPABILITIES_BY_ROLE.get((role or "").strip().lower(), frozenset())


# ─────────────────── Policies produit de l'organisation ───────────────────
#
# ## Deux concepts voisins qu'il ne faut jamais confondre
#
#     CAPACITE D'ADMINISTRATION          POLICY PRODUIT
#     `identity_manage`, `policy_manage` `ai_skills_enabled`
#     ce qu'un ADMINISTRATEUR peut       ce que l'ORGANISATION autorise
#     modifier dans le Control Plane     a ses membres dans Nova
#
# Les fusionner serait tentant — ce sont deux dictionnaires de booleens — et
# ce serait une faute. Une capacite d'administration protege la console ; une
# policy configure le produit. `policy_manage` dit qui a le droit de changer
# `ai_skills_enabled` ; il n'entre jamais dans le calcul de ce que voit un
# membre.
#
# ## La formule
#
#     capacite de base  ∩  policy de l'organisation  =  capacite effective
#
# L'intersection va dans un seul sens : une policy **restreint**, elle
# n'accorde jamais. Une organisation ne peut pas s'attribuer une fonction que
# le produit ne fournit pas — sans quoi la policy deviendrait une source de
# capacites, et le catalogue produit cesserait d'etre la reference.
#
# ## Ce qu'une policy ne pourra jamais toucher
#
# Ni SSO, ni step-up, ni sessions d'administration, ni fournisseurs, ni
# decouverte, ni roles, ni audit, ni acces a Nova Admin. Ce sont des fonctions
# du plan d'identite : une policy produit qui pourrait les eteindre serait un
# moyen de se verrouiller dehors — ou pire, de desarmer l'audit depuis un
# ecran de configuration. La separation est structurelle : ces fonctions ne
# figurent dans aucune cle de POLICY_SETTINGS, et un test le verifie.

POLICY_SCHEMA_VERSION = 2


class PolicySetting:
    """Une cle de policy declaree : son type, son defaut, ce qu'elle gouverne.

    Le document est stocke en JSON, mais il n'est **jamais arbitraire**. Le
    serveur possede le schema ; une cle inconnue est refusee a l'ecriture, un
    type invalide aussi. Un dictionnaire libre finirait par contenir la moitie
    de la configuration du produit sans que personne ne sache la relire.
    """

    def __init__(self, key: str, kind: type, default, capability: str | None):
        self.key = key
        self.kind = kind
        self.default = default
        # La capacite produit que cette policy contraint, cote poste. `None`
        # pour une policy qui n'en gouverne aucune directement.
        self.capability = capability


# ## Ce qui entre ici, et ce qui n'y entre pas
#
# Une policy n'est legitime que si la fonction existe vraiment, si l'on sait
# exactement ce que son refus signifie, et si ce refus peut etre applique
# ailleurs que dans l'affichage. Declarer des interrupteurs « pour plus tard »
# produirait un panneau dont la plupart des lignes ne feraient rien — et l'on
# ne saurait bientot plus lesquelles comptent.
#
# Les quatre cles ajoutees en Phase 30 gouvernent chacune une route serveur
# reelle, ce qui rend le refus opposable a n'importe quel client :
#
#     organization_vocabulary_enabled  /api/vocabulary, /api/dictionary/*
#     voice_commands_enabled           /api/command
#     engineering_notes_enabled        /api/engineering-notes
#     file_import_enabled              /api/import/transcribe
#
# **`file_import_enabled` ne touche jamais `/api/transcribe`.** Cette route
# sert aussi la dictee micro : la fermer sur une policy d'import couperait le
# coeur du produit. D'ou une route d'import distincte, sur le meme moteur.
POLICY_SETTINGS: dict[str, PolicySetting] = {
    setting.key: setting
    for setting in (
        PolicySetting("ai_skills_enabled", bool, True, "aiSkills"),
        PolicySetting("organization_vocabulary_enabled", bool, True, "dictionary"),
        PolicySetting("voice_commands_enabled", bool, True, "commands"),
        PolicySetting("engineering_notes_enabled", bool, True, "engineeringNotes"),
        PolicySetting("file_import_enabled", bool, True, "fileTranscription"),
    )
}

# Le defaut, ecrit une fois et derive du schema : deux listes a tenir a jour
# finiraient par diverger, et c'est le defaut qui decide du sort des
# organisations existantes.
#
# **Tous les defauts valent `True`.** Une organisation qui n'a jamais rien
# configure doit se comporter exactement comme avant l'existence des policies.
# Une cle qui apparait dans le schema ne doit rien eteindre chez personne.
DEFAULT_ORGANIZATION_POLICY: dict = {
    key: setting.default for key, setting in POLICY_SETTINGS.items()
}


def validate_policy_settings(candidate: dict) -> dict:
    """Le document propose est-il valide ? Renvoie les **surcharges** retenues.

    ## Pourquoi ne stocker que les surcharges

    Ce qui revient n'est pas le document complet mais uniquement les cles dont
    la valeur differe du defaut. Recopier les defauts en base les figerait :
    le jour ou un defaut produit change, les organisations qui n'avaient rien
    demande garderaient l'ancien comportement sans que personne ne l'ait choisi.

    Une console qui renvoie l'etat complet obtient donc gratuitement le
    « revenir au defaut » : reposer une valeur sur son defaut efface la
    surcharge.

    Leve `ValueError` avec un motif portant la cle fautive.
    """
    if not isinstance(candidate, dict):
        raise ValueError("POLICY_SETTINGS_INVALID")
    unknown = set(candidate) - set(POLICY_SETTINGS)
    if unknown:
        # Refuser plutot qu'ignorer : une cle mal orthographiee qui disparait
        # en silence donne a l'administrateur la certitude d'avoir configure
        # quelque chose qui ne s'appliquera jamais.
        raise ValueError(f"POLICY_KEY_UNKNOWN:{sorted(unknown)[0]}")
    overrides = {}
    for key, value in candidate.items():
        setting = POLICY_SETTINGS[key]
        # `isinstance(True, int)` vaut vrai : sans ce controle, un booleen
        # passerait pour un entier et reciproquement.
        if setting.kind is bool and not isinstance(value, bool):
            raise ValueError(f"POLICY_VALUE_INVALID:{key}")
        if not isinstance(value, setting.kind):
            raise ValueError(f"POLICY_VALUE_INVALID:{key}")
        if value != setting.default:
            overrides[key] = value
    return overrides


def read_policy_settings(raw: str | None) -> dict:
    """Relit les surcharges stockees, sans jamais laisser tomber le serveur.

    En **lecture**, l'inconnu est ignore et l'invalide est ecarte. La
    dissymetrie avec l'ecriture est voulue : une ligne ecrite par une version
    ulterieure — ou abimee — doit se degrader vers un comportement sur, pas
    empecher l'organisation de fonctionner.
    """
    try:
        stored = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    overrides = {}
    for key, setting in POLICY_SETTINGS.items():
        if key not in stored:
            continue
        value = stored[key]
        if setting.kind is bool and not isinstance(value, bool):
            continue
        if not isinstance(value, setting.kind):
            continue
        overrides[key] = value
    return overrides


def effective_policy_settings(overrides: dict) -> dict:
    """defauts ⊕ surcharges = policy effective.

    Le seul endroit ou les deux se rencontrent. Laisser chaque appelant faire
    son propre `overrides.get(key, default)` finirait par produire des defauts
    divergents selon la route.
    """
    resolved = dict(DEFAULT_ORGANIZATION_POLICY)
    resolved.update(overrides)
    return resolved


def organization_policy(organization_id: str, conn=None) -> dict:
    """La policy d'une organisation — toujours une reponse, jamais `None`.

    Renvoie **les deux** formes, et la distinction compte :

    - `overrides` : ce qu'un administrateur a explicitement decide ;
    - `settings`  : ce qui s'applique reellement, defauts compris.

    Une organisation sans ligne obtient les defauts a la **revision 0**. Cette
    revision-la est significative : elle dit « rien n'a jamais ete configure »,
    et une premiere ecriture partira donc de 0 pour produire la revision 1.
    """
    def load(connection):
        return connection.execute(
            "SELECT schema_version, revision, settings, updated_at, "
            "updated_by_user_id FROM organization_policies WHERE organization_id=?",
            (organization_id,),
        ).fetchone()

    row = load(conn) if conn is not None else None
    if conn is None:
        with db() as connection:
            row = load(connection)
    if row is None:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "revision": 0,
            "overrides": {},
            "settings": dict(DEFAULT_ORGANIZATION_POLICY),
            "updated_at": None,
            "updated_by_user_id": None,
            "configured": False,
        }
    overrides = read_policy_settings(row["settings"])
    return {
        "schema_version": row["schema_version"],
        "revision": row["revision"],
        "overrides": overrides,
        "settings": effective_policy_settings(overrides),
        "updated_at": row["updated_at"],
        "updated_by_user_id": row["updated_by_user_id"],
        "configured": True,
    }


def resolve_effective_product_capabilities(
    base_capabilities: dict, policy_settings: dict
) -> dict:
    """capacite de base ∩ policy = capacite effective.

    ## L'ordre de composition

        support produit  ∩  policy d'organisation  ∩  restriction de contexte
                                                   =  capacite effective

    Les restrictions de contexte — le mode pedagogique, par exemple — sont
    **deja** dans `base_capabilities` : c'est ainsi que le mode `classroom`
    ferme les commandes et les notes d'ingenierie. L'intersection les preserve
    donc sans traitement particulier : une policy permissive ne peut pas
    rouvrir ce qu'un mode a ferme, parce que `False and True` vaut `False`.

    C'est la propriete qui compte, et elle vaut mieux qu'une regle de priorite
    ecrite quelque part : il n'y a rien a se rappeler.

    **Autorite unique.** Le poste porte le meme resolveur pour son interface,
    mais toute action qui passe par le serveur est tranchee ici. Deux formules
    qui divergeraient produiraient un bouton actif menant a un refus — ou, bien
    pire, l'inverse.
    """
    effective = dict(base_capabilities)
    for setting in POLICY_SETTINGS.values():
        if setting.capability is None:
            continue
        if setting.capability not in effective:
            continue
        effective[setting.capability] = bool(
            effective[setting.capability]
        ) and bool(policy_settings.get(setting.key, setting.default))
    return effective


# ──────────────────── Packages d'organisation ─────────────────────────────
#
# ## Policy et Package repondent a deux questions differentes
#
#     POLICY                              PACKAGE
#     `organization_vocabulary_enabled`   « Vocabulaire Ingenierie v3 »
#     cette capacite est-elle autorisee ? quel contenu l'organisation distribue-t-elle ?
#
# Les confondre produirait un systeme ou desactiver une fonction demanderait de
# depublier du contenu, et ou publier du contenu accorderait une permission.
# **Un package ne porte donc aucun `enabled`** : la permission vit dans la
# policy, et un package actif reste inutilisable si la policy est fermee.
#
# ## Package et Version
#
#     Package  « Vocabulaire Ingenierie »   ce qui a un nom et une identite
#     Version  v1, v2, v3                   ce qui a un contenu
#
# Une seule version est **active** a la fois. Publier ne modifie jamais une
# version existante : on cree un brouillon, on le publie, et l'active change.
# Revenir en arriere ne reecrit rien non plus — c'est l'active qui redesigne
# une version anterieure, restee intacte.
#
# C'est ce qui rend le retour arriere sur : ce que l'on republie est
# exactement ce qui avait ete publie, et non une reconstruction de memoire.

PACKAGE_TYPE_STYLE = "style"
PACKAGE_TYPE_AI_SKILL = "ai_skill"
PACKAGE_TYPE_VOCABULARY = "vocabulary"
PACKAGE_TYPES = (PACKAGE_TYPE_STYLE, PACKAGE_TYPE_AI_SKILL, PACKAGE_TYPE_VOCABULARY)

PACKAGE_STATUS_DRAFT = "draft"
PACKAGE_STATUS_PUBLISHED = "published"
PACKAGE_STATUS_ARCHIVED = "archived"
PACKAGE_STATUSES = (PACKAGE_STATUS_DRAFT, PACKAGE_STATUS_PUBLISHED,
                    PACKAGE_STATUS_ARCHIVED)

VERSION_STATUS_DRAFT = "draft"
VERSION_STATUS_PUBLISHED = "published"

# Version du **format de contenu**, distincte du numero de version du package.
# La version 8 d'un package peut parfaitement porter un contenu de schema 1 :
# l'une compte les publications, l'autre decrit la forme. Les lier obligerait a
# republier pour changer de format, ou a changer de format pour republier.
# Passe a 2 en Phase 31B : un AI Skill porte desormais une `instruction`, et
# elle est obligatoire. Sans elle, un Skill n'aurait rien a dire au modele — ce
# serait une fiche, pas une action. Le numero de version du package, lui, ne
# bouge pas : les deux notions restent independantes.
PACKAGE_CONTENT_SCHEMA_VERSION = 2

# Limites, appliquees **par le serveur**. Une limite dans l'interface seule
# n'est pas une limite : elle indique une intention a qui veut bien la lire.
# SQLite stocke ces documents en ligne ; sans plafond, un package finirait par
# ralentir chaque lecture de la table.
PACKAGE_NAME_MAX = 120
PACKAGE_DESCRIPTION_MAX = 500
PACKAGE_CONTENT_MAX_BYTES = 256 * 1024
VOCABULARY_ENTRY_MAX = 2000
VOCABULARY_TERM_MAX = 200
STYLE_INSTRUCTION_MAX = 8000
AI_SKILL_TEXT_MAX = 4000
AI_SKILL_STEPS_MAX = 20
AI_SKILL_INSTRUCTION_MAX = 8000


class PackageValidationError(ValueError):
    """Refus structure : un code, un champ, un message affichable.

    Jamais une trace d'execution. Un administrateur qui corrige un package a
    besoin de savoir **quel champ** et **pourquoi** ; l'etat interne du serveur
    ne l'aide pas et le renseignerait sur son implementation.
    """

    def __init__(self, code: str, field: str, message: str):
        super().__init__(f"{code}:{field}")
        self.code = code
        self.field = field
        self.message = message

    def as_detail(self) -> dict:
        return {
            "code": "PACKAGE_VALIDATION_FAILED",
            "reason": self.code,
            "field": self.field,
            "message": self.message,
        }


def _text(value, field: str, maximum: int, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise PackageValidationError("not_a_string", field, "This field must be text.")
    cleaned = value.strip()
    if required and not cleaned:
        raise PackageValidationError("required", field, "This field is required.")
    if len(cleaned) > maximum:
        raise PackageValidationError(
            "too_long", field, f"This field is limited to {maximum} characters."
        )
    # Les caracteres de controle n'ont aucun sens dans un contenu declaratif et
    # servent surtout a masquer du texte a la relecture.
    if any(ord(character) < 32 and character not in "\n\t" for character in cleaned):
        raise PackageValidationError(
            "control_characters", field, "This field contains invalid characters."
        )
    return cleaned


def validate_style_content(content: dict) -> dict:
    """Un Style d'organisation : un nom, une consigne.

    Volontairement proche de `LLMPrompt` cote poste — `{id, name, prompt}` —
    pour qu'un Style publie se consomme sans traduction. L'identifiant, lui,
    est attribue par Nova (§ identifiants), jamais par l'auteur.
    """
    return {
        "name": _text(content.get("name"), "name", PACKAGE_NAME_MAX),
        "instruction": _text(
            content.get("instruction"), "instruction", STYLE_INSTRUCTION_MAX
        ),
    }


def validate_ai_skill_content(content: dict) -> dict:
    """Une action IA : ce qu'elle fait, et ce qu'elle dit au modele.

    **Declaratif, et rien d'autre.** Pas de script, pas d'URL a executer, pas
    d'interpolation de secret. Un package est du contenu distribue a des
    postes : lui laisser porter du code executable ferait de la console
    d'administration un moyen d'executer du code chez chaque membre — ce qui
    n'est pas une fonction produit mais une porte.
    """
    steps = content.get("steps") or []
    if not isinstance(steps, list):
        raise PackageValidationError("not_a_list", "steps", "Steps must be a list.")
    if len(steps) > AI_SKILL_STEPS_MAX:
        raise PackageValidationError(
            "too_many", "steps", f"At most {AI_SKILL_STEPS_MAX} steps."
        )
    duration = content.get("duration_minutes", 5)
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise PackageValidationError(
            "not_an_integer", "duration_minutes", "Duration must be a whole number."
        )
    if not 1 <= duration <= 240:
        raise PackageValidationError(
            "out_of_range", "duration_minutes", "Duration must be between 1 and 240."
        )
    return {
        "title": _text(content.get("title"), "title", PACKAGE_NAME_MAX),
        "summary": _text(content.get("summary"), "summary", AI_SKILL_TEXT_MAX),
        # **Obligatoire.** C'est ce qui distingue une action d'une fiche : sans
        # instruction, il n'y a rien a executer, et publier un Skill inerte
        # ferait croire a une fonction qui ne fera jamais rien.
        "instruction": _text(
            content.get("instruction"), "instruction", AI_SKILL_INSTRUCTION_MAX
        ),
        # Facultatifs : ils aident la personne, ils ne parlent pas au modele.
        "practice": _text(content.get("practice"), "practice", AI_SKILL_TEXT_MAX,
                          required=False),
        "duration_minutes": duration,
        "steps": [
            _text(step, f"steps[{index}]", AI_SKILL_TEXT_MAX)
            for index, step in enumerate(steps)
        ],
    }


def validate_vocabulary_content(content: dict) -> dict:
    """Une liste de termes et de leurs remplacements.

    Les doublons sont refuses plutot que silencieusement dedupliques : deux
    lignes pour le meme terme signifient que quelqu'un attend deux resultats
    differents, et en choisir un sans le dire produirait un comportement que
    personne ne saurait expliquer.
    """
    entries = content.get("entries")
    if not isinstance(entries, list):
        raise PackageValidationError("not_a_list", "entries", "Entries must be a list.")
    if not entries:
        raise PackageValidationError(
            "required", "entries", "A vocabulary needs at least one entry."
        )
    if len(entries) > VOCABULARY_ENTRY_MAX:
        raise PackageValidationError(
            "too_many", "entries", f"At most {VOCABULARY_ENTRY_MAX} entries."
        )
    seen = set()
    cleaned = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PackageValidationError(
                "not_an_object", f"entries[{index}]", "Each entry must be an object."
            )
        term = _text(entry.get("term"), f"entries[{index}].term", VOCABULARY_TERM_MAX)
        replacement = _text(
            entry.get("replacement"),
            f"entries[{index}].replacement",
            VOCABULARY_TERM_MAX,
        )
        key = term.casefold()
        if key in seen:
            raise PackageValidationError(
                "duplicate", f"entries[{index}].term", f"'{term}' appears twice."
            )
        seen.add(key)
        cleaned.append({"term": term, "replacement": replacement})
    return {"entries": cleaned}


PACKAGE_VALIDATORS = {
    PACKAGE_TYPE_STYLE: validate_style_content,
    PACKAGE_TYPE_AI_SKILL: validate_ai_skill_content,
    PACKAGE_TYPE_VOCABULARY: validate_vocabulary_content,
}


def validate_package_content(package_type: str, content) -> dict:
    """Valide **structurellement**, pas seulement syntaxiquement.

    « Le JSON se parse » ne veut rien dire : un document vide se parse aussi.
    Chaque type declare ses champs, ses limites et ses interdits, et ce qui
    ressort est un document normalise — pas celui qui est entre.
    """
    if package_type not in PACKAGE_VALIDATORS:
        raise PackageValidationError(
            "unknown_type", "package_type", "This package type is not supported."
        )
    if not isinstance(content, dict):
        raise PackageValidationError(
            "not_an_object", "content", "Content must be an object."
        )
    normalized = PACKAGE_VALIDATORS[package_type](content)
    encoded = json.dumps(normalized, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > PACKAGE_CONTENT_MAX_BYTES:
        raise PackageValidationError(
            "too_large", "content",
            f"Content is limited to {PACKAGE_CONTENT_MAX_BYTES // 1024} KB.",
        )
    return normalized


def normalize_draft_content(package_type: str, content) -> tuple[dict, str | None]:
    """Un brouillon a le droit d'etre incomplet.

    ## Pourquoi deux niveaux de validation

    Un brouillon commence vide. Lui appliquer la validation de publication
    rendrait sa creation impossible : il faudrait l'avoir rempli pour avoir le
    droit de commencer a le remplir. C'est le defaut que la recette de la
    Phase 31B a trouve — aucun test ne pouvait le voir, tous partaient d'un
    document deja complet.

    Ce qui reste applique au brouillon : les types, les cles declarees, les
    limites de taille. Ce qui attend la publication : les champs obligatoires.
    Autrement dit, on refuse ce qui est **faux**, on tolere ce qui est
    **inacheve**.

    Renvoie le document normalise et, le cas echeant, le motif qui empechera la
    publication — pour que la console puisse le dire avant que l'on clique.
    """
    if package_type not in PACKAGE_VALIDATORS:
        raise PackageValidationError(
            "unknown_type", "package_type", "This package type is not supported.")
    if not isinstance(content, dict):
        raise PackageValidationError(
            "not_an_object", "content", "Content must be an object.")

    # Le document est conserve tel quel, cles inconnues retirees : le brouillon
    # doit pouvoir etre relu exactement comme il a ete saisi.
    allowed = set(PACKAGE_DRAFT_FIELDS[package_type])
    unknown = set(content) - allowed
    if unknown:
        raise PackageValidationError(
            f"POLICY_KEY_UNKNOWN", sorted(unknown)[0],
            "This field is not part of this package type.")
    draft = {key: value for key, value in content.items() if key in allowed}

    encoded = json.dumps(draft, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > PACKAGE_CONTENT_MAX_BYTES:
        raise PackageValidationError(
            "too_large", "content",
            f"Content is limited to {PACKAGE_CONTENT_MAX_BYTES // 1024} KB.")

    try:
        validate_package_content(package_type, draft)
        return draft, None
    except PackageValidationError as error:
        if error.code not in DRAFT_TOLERATED_CODES:
            # Un type faux, une limite depassee, un doublon : ce n'est pas un
            # brouillon inacheve, c'est un document errone. Le garder ferait
            # decouvrir la faute au moment de publier — c'est-a-dire au plus
            # mauvais moment.
            raise
        # Inacheve, pas faux : on garde le brouillon et on retient pourquoi il
        # n'est pas encore publiable.
        return draft, f"{error.code}:{error.field}"


# Le seul motif qu'un brouillon a le droit de porter : un champ pas encore
# rempli. Tout le reste — type invalide, limite depassee, doublon — est une
# erreur, et la signaler tout de suite vaut mieux que de la laisser surgir au
# moment de publier.
DRAFT_TOLERATED_CODES = frozenset({"required"})

# Les cles qu'un brouillon a le droit de porter, par type. Utilisees pour
# retirer l'inconnu sans exiger le complet.
PACKAGE_DRAFT_FIELDS = {
    PACKAGE_TYPE_STYLE: ("name", "instruction"),
    PACKAGE_TYPE_AI_SKILL: ("title", "summary", "instruction", "practice",
                            "duration_minutes", "steps"),
    PACKAGE_TYPE_VOCABULARY: ("entries",),
}


def package_content_hash(content: dict) -> str:
    """Empreinte du contenu normalise.

    Pas une signature — rien ici ne prouve *qui* a publie. Elle detecte la
    corruption et permet a un poste de savoir qu'un contenu n'a pas change sans
    le retelecharger. Une vraie signature demanderait une gestion de cles, et
    en fabriquer une a moitie donnerait une assurance que le mecanisme ne tient
    pas.
    """
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def package_identifier(package_type: str, package_id: str) -> str:
    """Identifiant expose au poste, impossible a confondre.

    Les Styles integres portent `nova_style_…`, les Styles personnels un
    identifiant libre. Un Style d'organisation prend donc un prefixe qui
    n'appartient qu'a lui : sans cela, un package pourrait masquer un preset
    integre — ou en heriter les privileges de palier.
    """
    return f"org_{package_type}_{package_id}"


# Le prefixe des Styles d'organisation, derive de la meme regle que les
# identifiants eux-memes. Le poste porte la meme constante
# (`licensing::ORGANIZATION_STYLE_PREFIX`) : deux litteraux qui divergeraient
# feraient echouer la resolution d'un cote sans que l'autre le sache.
ORGANIZATION_STYLE_PREFIX = f"org_{PACKAGE_TYPE_STYLE}_"


def organization_policy_for_account(account) -> dict:
    """La policy applicable a un compte, cadree par **son** organisation.

    Jamais par un identifiant venu du client : ce serait un moyen de lire — ou
    de subir — la configuration d'une autre organisation.
    """
    organization_id = (
        account["organization_id"] if account is not None else None
    ) or nova_organization_id()
    return organization_id, organization_policy(organization_id)


def product_capability(capability: str, feature: str):
    """Garde de route, posee en dependance.

    En dependance plutot qu'en corps de fonction : la plupart des routes
    concernees recoivent une adresse (`current_user`), pas un compte, et leur
    faire changer de signature pour un controle transversal aurait touche sept
    routes sans rapport entre elles. La garde se lit aussi mieux au-dessus de
    la route qu'enfouie dans son corps.
    """
    def guard(account=Depends(current_account)):
        require_product_capability(account, capability, feature)

    return guard


def require_product_capability(account, capability: str, feature: str):
    """Refuse l'operation si l'organisation a ferme la fonction.

    Le poste masque deja l'entree correspondante, mais un poste est une
    interface : il peut etre ancien, modifie, ou appeler la route directement.
    Quand la fonction passe par le serveur, c'est au serveur de refuser.

    Le refus porte un code stable et le nom de la fonction — de quoi afficher
    un message juste — et rien d'autre : ni etat interne, ni configuration.
    """
    organization_id, _ = organization_policy_for_account(account)
    if not public_campus_config(organization_id)["capabilities"].get(capability):
        raise HTTPException(
            403, {"code": "ORGANIZATION_POLICY_DISABLED", "feature": feature}
        )


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


def security_role_for(row) -> str:
    """Rôle de sécurité Nova.

    Lu dans une colonne écrite par un opérateur, et **uniquement** là. Aucun
    métier, aucun groupe, aucun domaine d'adresse, aucune revendication d'IdP
    (`role=admin`, `groups=admins`, `is_admin=true`) et aucun champ envoyé par
    un client ne peut l'élever. Microsoft, Google et l'OIDC prouvent qui est la
    personne ; Nova seul décide de ce qu'elle a le droit de faire.

    Une valeur inconnue retombe sur `member` : une colonne corrompue ou écrite
    par une version future ne doit jamais accorder plus que le minimum.
    """
    if "security_role" not in row.keys():
        return SECURITY_ROLE_MEMBER
    role = (row["security_role"] or "").strip().lower()
    return role if role in SECURITY_ROLES else SECURITY_ROLE_MEMBER


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
    external_issuer: str = "",
    organization_id: str | None = None,
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
    organization_id = organization_id or nova_organization_id()
    with db() as conn:
        # L'identité pointe vers le compte, pas seulement vers son adresse :
        # c'est ce qui permet à une réauthentification de prouver qu'elle
        # concerne bien la même personne, et non un homonyme.
        account = user_by_email(conn, organization_id, email)
        user_id = account["user_id"] if account is not None else None
        conn.execute(
            "INSERT OR IGNORE INTO federated_identities(provider, external_subject, "
            "external_tenant_id, organization_id, email, user_id, verification, "
            "external_issuer, created_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                provider,
                external_subject,
                external_tenant_id,
                organization_id,
                email,
                user_id,
                verification,
                external_issuer,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE federated_identities SET last_seen=?, email=?, external_tenant_id=?, "
            "verification=?, user_id=COALESCE(?, user_id) "
            "WHERE organization_id=? AND provider=? AND external_issuer=? "
            "AND external_subject=?",
            (now, email, external_tenant_id, verification, user_id, organization_id,
             provider, external_issuer, external_subject),
        )
        conn.execute(
            "UPDATE users SET identity_provider=?, external_subject=?, external_tenant_id=? "
            "WHERE organization_id=? AND user_id=?",
            (provider, external_subject, external_tenant_id, organization_id, user_id),
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
        # Le jeton porte son identité et son organisation : il mène directement
        # au compte, sans passer par une recherche d'adresse qui, en
        # multi-tenant, pourrait tomber sur l'homonyme d'un autre tenant.
        session = conn.execute(
            "SELECT email, user_id, organization_id, expires_at FROM tokens "
            "WHERE token_hash=? AND active=1", (token_hash,)
        ).fetchone()
        if not session:
            raise HTTPException(401, "Token invalide ou révoqué")
        organization_id = session["organization_id"] or nova_organization_id()
        account = (
            user_by_id(conn, session["user_id"]) if session["user_id"]
            # Jeton antérieur à cette phase, dont le compte n'a jamais été
            # créé : la recherche reste **cadrée par l'organisation du jeton**.
            else user_by_email(conn, organization_id, session["email"])
        )
    if account is not None:
        if account["disabled"] or (account["status"] or "active") != "active":
            raise HTTPException(403, "Compte suspendu — contactez votre administrateur")
    if session["expires_at"] and session["expires_at"] < time.time():
        with db() as conn:
            conn.execute("UPDATE tokens SET active=0 WHERE token_hash=?", (token_hash,))
        raise HTTPException(401, "Session expiree")
    email = account["email"] if account is not None else session["email"]
    # Le profil se crée à la volée s'il n'existe pas encore : comportement
    # historique, conservé.
    touch_user(email, organization_id)
    return email

def current_account(authorization: str = Header(default="")):
    """Le **compte** que désigne le jeton, et non seulement son adresse.

    `current_user` renvoie une adresse — c'est ce qu'attendent quarante routes,
    et le contrat ne change pas. Mais une adresse ne suffit plus à retrouver
    quelqu'un : il faut savoir dans quelle organisation chercher. Les routes qui
    ont besoin du compte lui-même passent donc par ici, où le jeton mène
    directement à son `user_id`.
    """
    if not REQUIRE_AUTH:
        return None
    email = current_user(authorization)
    token_hash = hash_token(authorization.removeprefix("Bearer ").strip())
    with db() as conn:
        session = conn.execute(
            "SELECT user_id, organization_id FROM tokens WHERE token_hash=? AND active=1",
            (token_hash,),
        ).fetchone()
        if session and session["user_id"]:
            account = user_by_id(conn, session["user_id"])
            if account is not None:
                return account
        return user_by_email(
            conn,
            (session["organization_id"] if session else None) or nova_organization_id(),
            email,
        )


def issue_token(email: str, machine: str, organization_id: str | None = None) -> dict:
    """Ouvre une session Nova pour un utilisateur vérifié.

    La session est rattachée à un `user_id`, pas à une adresse : changer
    d'adresse ne doit pas déconnecter quelqu'un, et l'homonyme d'une autre
    organisation ne doit jamais partager ses compteurs d'appareils.
    """
    email = email.lower()
    organization_id = organization_id or nova_organization_id()
    now = time.time()
    # Le compte doit exister avant la session : c'est lui qui porte l'identité.
    touch_user(email, organization_id)
    with db() as conn:
        account = user_by_email(conn, organization_id, email)
        user_id = account["user_id"]
        # Reconnecting the same workstation replaces its previous session
        # instead of consuming another device slot.
        conn.execute(
            "UPDATE tokens SET active=0 WHERE user_id=? AND machine=?",
            (user_id, machine),
        )
        already_active = conn.execute(
            "SELECT 1 FROM tokens WHERE user_id=? AND active=1 AND "
            "(expires_at IS NULL OR expires_at>?) LIMIT 1",
            (user_id, now),
        ).fetchone()
        # Les sièges se comptent par personne — donc par `user_id`, et dans
        # l'organisation concernée.
        seats = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM tokens WHERE active=1 AND "
            "organization_id=? AND (expires_at IS NULL OR expires_at>?)",
            (organization_id, now),
        ).fetchone()["c"]
        if not already_active and seats >= MAX_SEATS:
            raise HTTPException(403, "Nombre maximal d'utilisateurs atteint")
        machines = conn.execute(
            "SELECT COUNT(*) c FROM tokens WHERE user_id=? AND active=1 AND "
            "(expires_at IS NULL OR expires_at>?)", (user_id, now)
        ).fetchone()["c"]
        if machines >= MAX_MACHINES_PER_EMAIL:
            raise HTTPException(403, "Nombre maximal d'appareils atteint")
        token = secrets.token_urlsafe(32)
        # Seule l'empreinte est écrite. Le jeton lui-même ne quitte cette
        # fonction que vers le client, et n'est jamais journalisé.
        digest = hash_token(token)
        expires_at = now + TOKEN_TTL_SECONDS
        conn.execute(
            "INSERT INTO tokens(token,token_hash,email,user_id,organization_id,"
            "machine,active,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"{TOKEN_HASH_MARKER}{digest}", digest, email, user_id,
             organization_id, machine, 1, now, expires_at),
        )
    return {"token": token, "expires_in": TOKEN_TTL_SECONDS}

def authenticated_token(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token manquant")
    return authorization.removeprefix("Bearer ").strip()

# ─────────────────────────── Administration moderne ───────────────────────
#
# ## Ce que cette section a remplacé
#
# Un jeton statique partagé, transmis en en-tête, authentifiait autrefois
# l'administration. Il désignait **une machine, pas une personne** : il ne se
# révoquait pas individuellement, ne disait pas qui avait agi, ne distinguait
# aucun rôle, et le même secret circulait entre tous ceux qui administraient. Un
# journal d'audit alimenté par lui ne pouvait écrire qu'une chose : « quelqu'un
# qui le détenait ». Il a été retiré en Phase 28.
#
# ## Le modèle
#
# L'identité vient du SSO de l'organisation — il n'existe aucun mot de passe
# Nova, aucune table de mots de passe d'administration, aucune réinitialisation
# maison. Microsoft, Google ou l'IdP OIDC prouvent qui est la personne ; Nova
# décide de ce qu'elle peut faire, en lisant `users.security_role`.
#
# ## Pourquoi une session distincte
#
# Une session Nova ordinaire sert à dicter, toute la journée, sur un poste. Si
# elle ouvrait aussi l'administration, le privilège élevé serait présent en
# permanence, sur chaque poste, pendant trente jours. La session
# d'administration s'ouvre donc explicitement depuis une session utilisateur
# valide, vit quelques heures, et se referme.

ADMIN_SESSION_TTL_SECONDS = int(
    os.getenv("NOVA_ADMIN_SESSION_TTL_SECONDS", str(4 * 3600))
)
ADMIN_SESSION_IDLE_SECONDS = int(
    os.getenv("NOVA_ADMIN_SESSION_IDLE_SECONDS", str(30 * 60))
)

# ── Réauthentification (step-up) ─────────────────────────────────────────
#
# Une session utilisateur valide ne prouve **pas** qu'une personne est devant le
# poste. Elle prouve qu'une authentification a eu lieu, peut-être il y a trois
# semaines, et qu'un jeton en est resté. C'est suffisant pour dicter ; ce ne
# l'est pas pour reconfigurer l'organisation.
#
# Nova n'invente aucun second facteur : il redemande une authentification à
# l'IdP et vérifie qu'elle est **récente**. Ce que l'IdP exige alors — mot de
# passe, MFA, clé d'accès, accès conditionnel — le regarde seul.

# Fenêtre de fraîcheur exigée. Cinq minutes : assez pour traverser un parcours
# MFA sans se presser, trop court pour qu'une session de navigateur oubliée la
# veille passe pour une présence.
ADMIN_STEP_UP_MAX_AGE_SECONDS = max(
    60, min(900, int(os.getenv("NOVA_ADMIN_STEP_UP_MAX_AGE_SECONDS", "300")))
)
# Tolérance d'horloge. Les horloges d'un IdP et d'un serveur divergent de
# quelques secondes ; les faire coïncider à la seconde produirait des refus
# inexplicables. Au-delà, un `auth_time` dans le futur n'est pas une horloge qui
# dérive, c'est une revendication qui ment.
ADMIN_STEP_UP_CLOCK_SKEW_SECONDS = 60


def authentication_is_fresh(auth_time, now: float | None = None) -> tuple[bool, str]:
    """L'authentification annoncée est-elle récente ?

    Trois refus distincts, et le premier est le plus important :

    - **`auth_time` absent** : le fournisseur n'a pas répondu à `max_age`, que la
      spécification OpenID Connect rend pourtant obligatoire dans ce cas. Sans
      cette revendication, « le jeton vient d'être émis » ne dit rien de la
      date à laquelle la personne s'est authentifiée — un IdP peut réémettre un
      jeton depuis une session vieille de plusieurs jours, sans rien demander à
      personne. On refuse : la sécurité passe avant la compatibilité ;
    - **trop ancienne** : la session d'IdP a servi telle quelle ;
    - **dans le futur** : au-delà de la tolérance d'horloge, la valeur est fausse.
    """
    if auth_time is None:
        return False, "auth_time_missing"
    try:
        auth_time = float(auth_time)
    except (TypeError, ValueError):
        return False, "auth_time_invalid"
    now = time.time() if now is None else now
    if auth_time > now + ADMIN_STEP_UP_CLOCK_SKEW_SECONDS:
        return False, "auth_time_in_future"
    if now - auth_time > ADMIN_STEP_UP_MAX_AGE_SECONDS + ADMIN_STEP_UP_CLOCK_SKEW_SECONDS:
        return False, "auth_time_too_old"
    return True, "ok"


class AdminPrincipal:
    """Qui agit, sur quelle organisation, avec quel rôle.

    Construit **par le serveur** à chaque appel, jamais désérialisé depuis une
    requête : aucun client ne peut annoncer son rôle ni son organisation.
    """

    def __init__(
        self,
        user_id: str | None,
        email: str | None,
        organization_id: str,
        security_role: str,
        session_id: str | None,
        auth_provider: str | None,
        issued_at: float,
    ):
        self.user_id = user_id
        self.email = email
        self.organization_id = organization_id
        self.security_role = security_role
        self.session_id = session_id
        self.auth_provider = auth_provider
        self.issued_at = issued_at

    @property
    def capabilities(self) -> frozenset[str]:
        return capabilities_for_role(self.security_role)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    # `is_legacy_token` vivait ici. Il designait un principal issu de l'en-tete
    # d'administration herite, chemin retire du code en Phase 33. Aucun appelant
    # ne subsistait — verifie sur le serveur et sur la console. Le garder
    # suggerait qu'un mode d'authentification disparu pouvait encore se
    # presenter, ce qui est la pire forme de code mort.


def new_request_id() -> str:
    """Identifiant de corrélation d'une action d'administration.

    Volontairement local : rien ici ne prétend être un traçage distribué.
    """
    return uuid.uuid4().hex


def record_admin_action(
    principal: AdminPrincipal,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict | None = None,
    request_id: str | None = None,
    conn=None,
):
    """Écrit une ligne d'audit. Ajout seul.

    `metadata` ne doit porter que des faits non sensibles — un type de
    fournisseur, un booléen, un mode. Jamais un secret, un jeton, un code
    d'autorisation, un `id_token`, un `nonce`, ni le moindre contenu dicté. Ce
    que l'on n'écrit pas ne peut pas fuir par un journal.

    ## `conn` : la mutation et sa trace dans la même transaction

    Passer la connexion de l'appelant fait entrer l'écriture d'audit **dans la
    transaction de la mutation**. Les deux réussissent ou aucune ne réussit.

    Sans elle — c'était le cas jusqu'à la Phase 27 — une panne entre les deux
    laissait une mutation sans trace. Moins grave qu'un journal affirmant une
    action qui n'a pas eu lieu, mais le journal servira un jour à répondre à
    « qui a désactivé ce compte ? », et une réponse incomplète y est mauvaise.

    Omettre `conn` reste possible pour les appels qui ne mutent rien —
    l'ouverture d'une session d'administration, par exemple.
    """
    if conn is not None:
        _write_audit_row(conn, principal, action, target_type, target_id,
                         metadata, request_id)
        return
    with db() as owned:
        _write_audit_row(owned, principal, action, target_type, target_id,
                         metadata, request_id)


def _write_audit_row(conn, principal, action, target_type, target_id,
                     metadata, request_id):
    conn.execute(
        "INSERT INTO admin_audit_log(id, organization_id, actor_user_id, "
        "actor_role, action, target_type, target_id, request_id, metadata, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex,
            principal.organization_id,
            principal.user_id,
            principal.security_role,
            action,
            target_type,
            target_id,
            request_id or new_request_id(),
            json.dumps(metadata, ensure_ascii=False) if metadata else None,
            time.time(),
        ),
    )


def create_admin_session(user_row, auth_provider: str | None) -> dict:
    """Ouvre une session d'administration pour un utilisateur déjà authentifié.

    Le jeton est opaque et de forte entropie — pas un JWT maison, dont la seule
    fonction ici serait de porter un rôle que le serveur relit de toute façon en
    base. Seule son empreinte est écrite : une copie de la base ne donne aucune
    session utilisable.
    """
    now = time.time()
    token = secrets.token_urlsafe(32)
    session_id = uuid.uuid4().hex
    role = security_role_for(user_row)
    with db() as conn:
        conn.execute(
            "INSERT INTO admin_sessions(id, user_id, email, organization_id, "
            "security_role, auth_provider, token_hash, created_at, expires_at, "
            "last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                user_row["user_id"],
                user_row["email"],
                user_row["organization_id"] or nova_organization_id(),
                role,
                auth_provider,
                hash_token(token),
                now,
                now + ADMIN_SESSION_TTL_SECONDS,
                now,
            ),
        )
    return {
        "session_id": session_id,
        "token": token,
        "role": role,
        "expires_at": now + ADMIN_SESSION_TTL_SECONDS,
    }


def revoke_admin_sessions_for_user(user_id: str, reason: str = "revoked"):
    """Ferme toutes les sessions d'administration d'un utilisateur.

    Appelée quand la personne se déconnecte de Nova : laisser une session
    privilégiée ouverte derrière un utilisateur parti demanderait une
    justification que nous n'avons pas.
    """
    if not user_id:
        return
    with db() as conn:
        conn.execute(
            "UPDATE admin_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (time.time(), user_id),
        )
    print(f"[admin] session revoked reason={reason}")


def set_security_role(email: str, role: str, organization_id: str | None = None) -> dict:
    """Attribue, change ou retire un rôle de sécurité.

    **Aucun endpoint ne l'expose.** Le premier administrateur d'une organisation
    ne peut pas naître d'un appel HTTP : un endpoint « faites de moi un
    administrateur » est une porte dérobée, quelles que soient les conditions
    qu'on lui ajoute. Cette fonction est appelée par un opérateur ayant déjà
    accès au serveur — voir `admin_cli.py`.

    Retirer un rôle ferme immédiatement les sessions d'administration de la
    personne : sans cela, la révocation ne prendrait effet qu'à l'expiration.
    """
    role = (role or "").strip().lower()
    if role not in SECURITY_ROLES:
        raise ValueError(f"rôle inconnu : {role}")
    email = email.strip().lower()
    organization_id = organization_id or nova_organization_id()
    with db() as conn:
        # Cadré à l'organisation : « le premier alex@example.com trouvé » n'est
        # pas une personne, c'est une coïncidence.
        row = user_by_email(conn, organization_id, email)
        if row is None:
            raise LookupError(f"aucun compte : {email}")
        conn.execute(
            "UPDATE users SET security_role=?, updated_at=? WHERE user_id=?",
            (role, time.time(), row["user_id"]),
        )
    if role not in ADMIN_SECURITY_ROLES:
        revoke_admin_sessions_for_user(row["user_id"], reason="role_revoked")
    return {"email": email, "security_role": role, "user_id": row["user_id"]}


def administrators_of(conn, organization_id: str):
    """Les comptes de l'organisation qui portent un role d'administration."""
    placeholders = ",".join("?" for _ in ADMIN_SECURITY_ROLES)
    return conn.execute(
        f"SELECT * FROM users WHERE organization_id=? AND security_role IN ({placeholders}) "
        f"ORDER BY normalized_email",
        (organization_id, *sorted(ADMIN_SECURITY_ROLES)),
    ).fetchall()


def identity_managers_of(conn, organization_id: str) -> list[str]:
    """Comptes **actifs** capables de gerer les identites de l'organisation.

    Ce sont eux qui peuvent attribuer et retirer des roles. Si la liste se vide,
    plus personne ne peut nommer d'administrateur : l'organisation ne se
    recupere alors que par la ligne de commande, sur le serveur.
    """
    return [
        row["user_id"]
        for row in administrators_of(conn, organization_id)
        if CAPABILITY_IDENTITY_MANAGE in capabilities_for_role(security_role_for(row))
        and account_status(row) == "active"
    ]


class LastIdentityManager(RuntimeError):
    """Le changement demande laisserait l'organisation sans gestionnaire."""


def change_security_role(
    organization_id: str, user_id: str, role: str, actor_user_id: str | None = None
) -> dict:
    """Change le role de securite d'un compte, par son identite.

    ## Pourquoi par `user_id`, et non par adresse

    Une adresse est un attribut mutable depuis la Phase 23. Muter un privilege
    en la designant reviendrait a viser une cible qui bouge — et, en
    multi-tenant, a viser l'homonyme d'une autre organisation.

    ## Le garde-fou

    Retirer `identity_manage` au dernier compte qui le porte fermerait la porte
    a toute nomination future : plus personne ne pourrait attribuer de role, y
    compris celui qui vient de cliquer. La seule issue serait la ligne de
    commande, sur le serveur. On refuse donc, comme on refuse de desactiver le
    dernier fournisseur.

    Ce n'est pas de la prudence excessive : le cas arrive naturellement quand un
    unique administrateur se retrograde « pour tester ».
    """
    role = (role or "").strip().lower()
    if role not in SECURITY_ROLES:
        raise ValueError(f"role inconnu : {role}")

    with db() as conn:
        target = user_by_id(conn, user_id)
        # Cadre au tenant : un identifiant d'ailleurs n'existe pas ici.
        if target is None or target["organization_id"] != organization_id:
            raise LookupError("compte introuvable")

        previous = security_role_for(target)
        if previous == role:
            return {"user_id": user_id, "previous_role": previous, "security_role": role,
                    "changed": False}

        managers = identity_managers_of(conn, organization_id)
        loses_management = CAPABILITY_IDENTITY_MANAGE not in capabilities_for_role(role)
        if loses_management and managers == [user_id]:
            raise LastIdentityManager(
                "au moins un administrateur d'organisation doit demeurer"
            )

        conn.execute(
            "UPDATE users SET security_role=?, updated_at=? WHERE user_id=?",
            (role, time.time(), user_id),
        )

    # Le role a change : les sessions ouvertes portent des capacites perimees.
    # Les fermer force un nouveau step-up, qui relira le role reel — plutot que
    # de laisser une session vivre avec des droits qu'elle n'a plus.
    revoke_admin_sessions_for_user(user_id, reason="role_changed")
    print(f"[admin] role changed target={user_id[:8]} to={role}")
    return {
        "user_id": user_id,
        "previous_role": previous,
        "security_role": role,
        "changed": True,
        "actor_user_id": actor_user_id,
    }


def resolve_admin_session(token: str) -> AdminPrincipal:
    """Valide un jeton d'administration, et **revérifie tout** au passage.

    Le rôle inscrit dans la session est indicatif. Ce qui fait autorité, c'est
    l'état actuel : compte actif, rôle toujours administrateur, organisation
    active. Une session ne fige pas un privilège — retirer le rôle d'une
    personne doit lui fermer la porte au prochain appel, pas au prochain mois.
    """
    now = time.time()
    with db() as conn:
        session = conn.execute(
            "SELECT * FROM admin_sessions WHERE token_hash=?", (hash_token(token),)
        ).fetchone()
        if session is None:
            raise auth_error(401, "ADMIN_REQUIRED")
        if session["revoked_at"] is not None:
            raise auth_error(401, "ADMIN_SESSION_REVOKED")
        if session["expires_at"] < now:
            raise auth_error(401, "ADMIN_SESSION_EXPIRED")
        idle_since = session["last_seen_at"] or session["created_at"]
        if now - idle_since > ADMIN_SESSION_IDLE_SECONDS:
            conn.execute(
                "UPDATE admin_sessions SET revoked_at=? WHERE id=?",
                (now, session["id"]),
            )
            raise auth_error(401, "ADMIN_SESSION_EXPIRED")

        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?", (session["user_id"],)
        ).fetchone()
        organization = conn.execute(
            "SELECT status FROM organizations WHERE id=?",
            (session["organization_id"],),
        ).fetchone()

    if user is None or account_status(user) != "active":
        raise auth_error(403, "ADMIN_REQUIRED")
    role = security_role_for(user)
    if role not in ADMIN_SECURITY_ROLES:
        # Le rôle a été retiré depuis l'ouverture de la session.
        raise auth_error(403, "ADMIN_ROLE_REQUIRED")
    if organization is not None and (organization["status"] or "active") != "active":
        raise auth_error(403, "ADMIN_REQUIRED")

    with db() as conn:
        conn.execute(
            "UPDATE admin_sessions SET last_seen_at=? WHERE id=?", (now, session["id"])
        )
    return AdminPrincipal(
        user_id=session["user_id"],
        email=session["email"],
        organization_id=session["organization_id"],
        security_role=role,
        session_id=session["id"],
        auth_provider=session["auth_provider"],
        issued_at=session["created_at"],
    )


def admin_principal(authorization: str = Header(default="")) -> AdminPrincipal:
    """Identifie l'administrateur d'un appel.

    **Un seul chemin** depuis la Phase 28 : une session d'administration, ouverte
    par SSO puis réauthentification.

    Le jeton statique partagé qui existait à côté a été retiré. Il authentifiait
    une machine et non une personne : il ne se révoquait pas individuellement —
    le changer déconnectait tout le monde — et un journal d'audit alimenté par
    lui ne pouvait écrire qu'une chose, « quelqu'un qui le détenait ».

    Le client n'annonce jamais « ce jeton est un jeton d'administration » : le
    serveur le sait par sa table. Un jeton de session utilisateur présenté ici
    est simplement introuvable dans `admin_sessions`, donc refusé.
    """
    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token:
            return resolve_admin_session(token)
    raise auth_error(401, "ADMIN_REQUIRED")


def require_admin_capability(capability: str):
    """Dépendance FastAPI exigeant une capacité précise.

    Une seule porte pour toutes les routes d'administration : la matrice est
    lisible en un endroit, et une route ne peut pas oublier une vérification
    qu'elle n'a pas à écrire.
    """

    def dependency(principal: AdminPrincipal = Depends(admin_principal)) -> AdminPrincipal:
        if not principal.can(capability):
            # Le refus ne dit pas quel rôle manquait : c'est inutile à un
            # administrateur légitime, et informatif pour quiconque sonde.
            raise auth_error(403, "ADMIN_ROLE_REQUIRED")
        return principal

    return dependency


def require_organization_access(principal: AdminPrincipal, organization_id: str) -> str:
    """L'organisation demandée est-elle celle que ce principal administre ?

    L'`organization_id` d'une URL est une **désignation**, jamais une
    autorisation. Sans cette vérification, il suffirait d'en changer dans le
    chemin pour administrer le voisin.
    """
    if organization_id != principal.organization_id:
        # Même réponse qu'une organisation inexistante : un administrateur n'a
        # pas à découvrir quelles autres organisations existent.
        raise auth_error(404, "ORGANIZATION_NOT_FOUND")
    return organization_id

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
    # Organization Styles are designated by id and resolved server-side. The
    # legacy prompt remains accepted for builtin/personal Styles so existing
    # Desktop versions keep working during migration.
    style_id: str | None = None
    style_prompt: str | None = None

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

def public_campus_config(organization_id: str | None = None) -> dict:
    """Ce que le poste apprend de l'organisation qu'il sert.

    ## Base, puis policy

    Les capacites annoncees ici sont le produit de deux choses : ce que le
    deploiement offre — le mode pedagogique, la presence d'un moteur — et ce
    que l'organisation autorise. La premiere est la **base**, la seconde la
    **policy**, et l'intersection se calcule dans
    `resolve_effective_product_capabilities`, jamais a la main.

    `organization_id` permet a `/api/me` d'appliquer la policy du membre plutot
    que celle de l'organisation active. En mode dedie les deux coincident ; en
    multi-tenant, non — et c'est exactement le genre d'ecart qui ferait
    repondre pour le voisin.
    """
    auth_methods = ["email_code"]
    if ENTRA_CLIENT_ID:
        auth_methods.append("entra")
    policy = organization_policy(organization_id or nova_organization_id())
    config = {
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
            # Derive du meme mode que `ai_skills.enabled` ci-dessus. Les deux
            # champs decrivent une seule decision : les laisser diverger — ce
            # qui etait le cas en mode examen, ou la capacite restait ouverte
            # pendant que le bloc la fermait — obligeait le poste a choisir
            # lequel croire.
            "aiSkills": EDUCATION_MODE != "assessment",
            "personalization": True,
        },
        "privacy": {
            "verified": True,
            "contentRetention": "not_stored",
            "usageCounters": "counts_only",
            "infrastructure": "campus",
        },
    }
    config["capabilities"] = resolve_effective_product_capabilities(
        config["capabilities"], policy["settings"]
    )
    # Le bloc historique `ai_skills` doit suivre la meme decision. Deux
    # reponses divergentes dans la meme charge utile — la capacite fermee, le
    # bloc ouvert — laisseraient le poste choisir laquelle croire.
    config["ai_skills"]["enabled"] = (
        config["ai_skills"]["enabled"] and config["capabilities"]["aiSkills"]
    )
    # Revision seule : le poste sait qu'une policy a change sans comparer le
    # document, et sans qu'aucun detail de gouvernance ne transite par un
    # endpoint public.
    config["policy_revision"] = policy["revision"]
    return config

# ---------------------------------------------------------------- app
app = FastAPI(title="Nova Server", version="0.2.0")

# Ce que le navigateur a le droit de demander. La liste doit couvrir **toutes**
# les methodes que l'API expose reellement : une methode oubliee ne produit pas
# une erreur lisible. Le navigateur refuse le controle prealable, `fetch` echoue
# sans reponse, et la console ne peut que l'annoncer comme une panne reseau —
# ce qui envoie chercher le probleme au mauvais endroit. C'est arrive en
# Phase 29 avec `PUT`, decouvert seulement dans un vrai navigateur.
#
# Declarees ici plutot qu'en ligne pour qu'un test puisse les confronter aux
# routes, meme quand aucune origine n'est configuree.
ADMIN_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
ADMIN_CORS_HEADERS = ["Authorization", "Content-Type"]

if NOVA_ADMIN_ORIGINS:
    # Liste explicite, et `allow_credentials` reste faux : la console porte son
    # jeton dans un en-tête, jamais dans un cookie ambiant. Rien n'est donc
    # envoyé automatiquement par le navigateur, et il n'y a pas de CSRF à
    # défendre sur ce chemin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=NOVA_ADMIN_ORIGINS,
        allow_credentials=False,
        allow_methods=ADMIN_CORS_METHODS,
        allow_headers=ADMIN_CORS_HEADERS,
        max_age=600,
    )
    print(f"[admin] web console origins={len(NOVA_ADMIN_ORIGINS)}")


@app.get("/api/health")
def health():
    """Vivacite du serveur, et rien d'autre.

    Chaque poste interroge cette route toutes les trente secondes pour decider
    s'il bascule en mode local. Y sonder le runtime d'inference ferait deux
    appels reseau supplementaires par poste et par cycle vers le moteur prive,
    et un modele en cours de chargement suffirait a faire decrocher tout le
    parc alors que le serveur repond parfaitement. L'etat reel du runtime est
    une information d'exploitation : elle est servie, authentifiee, par
    `/api/control/organization/ai-runtime`.
    """
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
    # Compte par adresse : c'est la ressource qu'un attaquant epuiserait, en
    # envoyant mille codes a la meme personne ou en balayant un annuaire.
    enforce_rate_limit("email", hash_token(email), ratelimit.AUTH_START)
    domain = email.split("@")[-1]
    if domain not in ALLOWED_DOMAINS:
        return {"sent": True}   # réponse identique : ne pas révéler les domaines
    with db() as conn:
        user = user_by_email(conn, nova_organization_id(), email)
    if user and user["disabled"]:
        return {"sent": True}   # compte suspendu : on ne dit rien
    code = f"{random.randint(0, 999999):06d}"
    with db() as conn:
        conn.execute("DELETE FROM auth_codes WHERE email=?", (email,))
        conn.execute("INSERT INTO auth_codes(email, code, expires_at, attempts, "
                     "organization_id) VALUES (?,?,?,0,?)",
                     (email, code, time.time() + CODE_TTL_SECONDS,
                      nova_organization_id()))
    try:
        send_code_email(email, code)
    except Exception as e:
        print(f"[SMTP] erreur envoi à {email}: {e}")
    return {"sent": True}

# --- auth : vérification du code -> token
@app.post("/api/auth/verify")
def auth_verify(body: VerifyCode):
    email = body.email.lower()
    # Le compteur par adresse existe deja en base (`attempts`), mais il ne
    # franchit pas la frontiere d'une adresse : mille adresses valent mille
    # essais. Celui-ci est partage entre les workers.
    enforce_rate_limit("email", hash_token(email), ratelimit.AUTH_VERIFY)
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
        row = user_by_email(conn, nova_organization_id(), user)
        conn.execute(
            "UPDATE tokens SET active=0 WHERE token_hash=? AND user_id=?",
            (hash_token(token), row["user_id"] if row else None),
        )
    # Une personne qui se déconnecte de Nova ne devrait pas laisser derrière elle
    # une session privilégiée ouverte. La session d'administration est dérivée de
    # la session utilisateur : elle en suit le sort.
    if row is not None:
        revoke_admin_sessions_for_user(row["user_id"], reason="user_logout")
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

# ------------------------------------------------- SSO Organization : PKCE
#
# ## Un seul moteur, plusieurs fournisseurs
#
# Microsoft Entra et Google Workspace partagent le même flux — Authorization
# Code + PKCE, navigateur système, retour en bouclage — et les mêmes garanties.
# Ce qui les distingue tient en quelques valeurs : où demander l'autorisation,
# où échanger le code, quel émetteur attendre, où trouver les clés publiques, et
# quelle revendication rattache l'utilisateur à une organisation.
#
# Ces différences vivent dans `ADAPTERS`. Tout le reste — état des tentatives,
# validation cryptographique, cache JWKS, rattachement de compte, émission de
# session — est commun et n'a été écrit qu'une fois. Ajouter un OIDC générique
# reviendra à ajouter une entrée, pas un second moteur.
#
# ## Frontière de confiance, identique pour tous
#
# Le poste ne prouve rien. Il génère son `code_verifier`, son `state` et son
# `nonce`, ouvre le navigateur, récupère un `code` — puis passe la main. C'est le
# serveur qui échange ce code auprès du fournisseur, valide cryptographiquement
# le jeton d'identité et décide de l'organisation. Il n'accepte jamais
# « fais-moi confiance, je suis X dans l'organisation Y ».


# Cache JWKS, indexé par URL de clés. La rotation est permanente chez les deux
# fournisseurs : on recharge à la demande lorsqu'un `kid` inconnu apparaît, sans
# jamais suivre une URL fournie par le jeton lui-même.
JWKS_CACHE: dict[str, dict] = {}
JWKS_CACHE_TTL_SECONDS = 3600

# Tolérance d'horloge pour `exp` / `nbf`, en secondes.
SSO_CLOCK_SKEW_SECONDS = 120


class SsoStartRequest(BaseModel):
    code_challenge: str
    nonce: str
    redirect_uri: str
    machine: str = "poste inconnu"
    # Désigne une configuration précise. Facultatif : les postes déployés
    # raisonnent encore par type de fournisseur.
    provider_config_id: str | None = None


class SsoExchangeRequest(BaseModel):
    flow_id: str
    code: str
    code_verifier: str
    redirect_uri: str


# Rétrocompatibilité : les postes déjà déployés parlent aux routes `entra`.
EntraPkceStartRequest = SsoStartRequest
EntraPkceExchangeRequest = SsoExchangeRequest


def auth_error(status: int, code: str) -> HTTPException:
    """Erreur d'authentification structurée.

    Le corps ne porte qu'un code : le poste choisit le libellé, et aucun détail
    technique — jeton, code d'autorisation, revendication — ne transite.
    """
    return HTTPException(status, code)


# ------------------------------------------------------------ fournisseurs


def entra_issuer(claims: dict, _config: dict | None = None) -> str:
    """Émetteur attendu chez Microsoft : il dépend du tenant."""
    return f"https://login.microsoftonline.com/{claims.get('tid', '')}/v2.0"


def entra_jwks_uri(claims: dict, _config: dict | None = None) -> str:
    """Clés publiques du tenant. Dérivée du `tid`, **jamais** d'un champ
    d'en-tête du jeton : un `jku` hostile ne joue aucun rôle."""
    return f"https://login.microsoftonline.com/{claims.get('tid', '')}/discovery/v2.0/keys"


def google_issuer(_claims: dict, _config: dict | None = None) -> str:
    # Google accepte deux formes ; la variante avec schéma est celle que
    # renvoient les jetons du point de terminaison v2.
    return "https://accounts.google.com"


def google_jwks_uri(_claims: dict, _config: dict | None = None) -> str:
    return "https://www.googleapis.com/oauth2/v3/certs"


def oidc_issuer_claim(_claims: dict, config: dict | None = None) -> str:
    """Émetteur attendu : celui de **cette** configuration, exactement."""
    return (config or {}).get("oidc_issuer") or ""


def oidc_jwks_uri(_claims: dict, config: dict | None = None) -> str:
    """Clés publiques, telles que la découverte les a annoncées.

    Le cache de métadonnées est déjà rempli au moment de la validation : la
    tentative a commencé par la découverte.
    """
    issuer = (config or {}).get("oidc_issuer") or ""
    cached = OIDC_METADATA_CACHE.get(issuer)
    return cached["metadata"]["jwks_uri"] if cached else ""


ADAPTERS: dict[str, dict] = {
    "microsoft_entra": {
        # Autorité `organizations` : comptes professionnels et scolaires
        # uniquement, jamais de compte Microsoft personnel. Le point d'entrée
        # est multi-tenant parce que Nova doit servir plusieurs
        # établissements — c'est le `tid` vérifié, rapproché de la table
        # `organizations`, qui décide de l'accès.
        "authorization_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
        "issuer": entra_issuer,
        "jwks_uri": entra_jwks_uri,
        # `oid` identifie l'objet utilisateur dans le tenant : immuable au
        # renommage, contrairement à `sub` qui dépend de l'application.
        "subject_claim": "oid",
        # Revendication qui rattache l'utilisateur à une organisation.
        "tenant_claim": "tid",
        "organization_column": "entra_tenant_id",
        "extra_authorize_params": {},
        # Client public : Microsoft n'attend aucun secret.
        "uses_client_secret": False,
        "algorithms": ["RS256"],
    },
    "google_workspace": {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "issuer": google_issuer,
        "jwks_uri": google_jwks_uri,
        # `sub` est unique parmi tous les comptes Google et n'est jamais
        # réattribué, même si l'adresse change.
        "subject_claim": "sub",
        # `hd` — le domaine hébergé du Workspace. Voir la note ci-dessous.
        "tenant_claim": "hd",
        "organization_column": "google_hosted_domain",
        # Invite Google à ne proposer que les comptes du domaine attendu. C'est
        # un confort d'interface : la vérification qui compte est faite sur le
        # jeton validé, jamais sur ce paramètre que le client peut modifier.
        "extra_authorize_params": {"access_type": "offline", "prompt": "select_account"},
        # Google émet un secret pour les clients « Desktop » et l'exige à
        # l'échange, tout en documentant qu'une application installée ne peut
        # pas garder de secret. Il est donc traité comme un identifiant public :
        # il ne vit que côté serveur, jamais dans Nova.exe.
        "uses_client_secret": True,
        "algorithms": ["RS256"],
    },
    "oidc": {
        # Découverts, pas configurés à la main : voir `provider_endpoints`.
        "authorization_endpoint": "",
        "token_endpoint": "",
        "issuer": oidc_issuer_claim,
        "jwks_uri": oidc_jwks_uri,
        "subject_claim": "sub",
        # Aucun rattachement par revendication : la configuration OIDC
        # **appartient** à une organisation. Voir `organization_for_tenant`.
        "tenant_claim": None,
        "organization_column": "oidc_issuer",
        "extra_authorize_params": {},
        # Décidé par la méthode d'authentification déclarée, pas en dur.
        "uses_client_secret": False,
        # RS256 seulement. Tous les IdP visés le proposent, et élargir
        # l'allowlist sans tests complets serait un pari, pas une compatibilité.
        "algorithms": ["RS256"],
    },
}


def provider_client_id(provider: str) -> str:
    return {
        "microsoft_entra": ENTRA_CLIENT_ID,
        "google_workspace": GOOGLE_CLIENT_ID,
        "oidc": OIDC_CLIENT_ID,
    }.get(provider, "")


def provider_client_secret(provider: str) -> str:
    return {
        "google_workspace": GOOGLE_CLIENT_SECRET,
        "oidc": OIDC_CLIENT_SECRET,
    }.get(provider, "")


def provider_scopes(provider: str) -> str:
    return {
        "microsoft_entra": ENTRA_PKCE_SCOPES,
        "google_workspace": GOOGLE_SCOPES,
        "oidc": OIDC_SCOPES,
    }.get(provider, "openid email profile")


def provider_requires_secret(provider: str) -> bool:
    """Le fournisseur exige-t-il un secret à l'échange ?

    Statique pour Microsoft (non) et Google (oui) ; déclaré pour l'OIDC
    générique, où la méthode dépend de l'IdP.
    """
    if provider == "oidc":
        return OIDC_CLIENT_AUTH_METHOD in {"client_secret_post", "client_secret_basic"}
    return ADAPTERS[provider]["uses_client_secret"]


def provider_display_name(provider: str) -> str:
    """Libellé du bouton. **Affichage uniquement.**"""
    return {
        "microsoft_entra": "Microsoft",
        "google_workspace": "Google",
        "oidc": OIDC_DISPLAY_NAME,
    }.get(provider, provider)


def require_provider(provider: str) -> dict:
    config = ADAPTERS.get(provider)
    if config is None:
        raise auth_error(404, "PROVIDER_UNKNOWN")
    if not provider_client_id(provider):
        raise auth_error(503, "PROVIDER_NOT_CONFIGURED")
    if provider_requires_secret(provider) and not provider_client_secret(provider):
        raise auth_error(503, "PROVIDER_NOT_CONFIGURED")
    if provider == "oidc":
        if not OIDC_ISSUER:
            raise auth_error(503, "PROVIDER_NOT_CONFIGURED")
        if OIDC_CLIENT_AUTH_METHOD not in {
            "none",
            "client_secret_post",
            "client_secret_basic",
        }:
            raise auth_error(500, "PROVIDER_CONFIG_INVALID")
    return config


def organization_for_provider(provider: str) -> database.Row | None:
    """Organisation de cette instance, si elle est rattachée à ce fournisseur.

    Le rattachement est **déclaré**, jamais déduit : sans valeur dans la colonne
    correspondante, le fournisseur reste indisponible.
    """
    column = ADAPTERS[provider]["organization_column"]
    with db() as conn:
        organization = conn.execute(
            "SELECT * FROM organizations WHERE id=?", (nova_organization_id(),)
        ).fetchone()
    if organization and organization[column]:
        return organization
    return None


def usable_provider_configs() -> list[dict]:
    """Configurations utilisables de l'organisation active.

    Une configuration désactivée ou invalide n'apparaît pas : mieux vaut aucun
    bouton qu'un bouton qui mène à une erreur incompréhensible.
    """
    return provider_configs_for_organization(nova_organization_id())


def configured_providers() -> list[str]:
    """Types de fournisseurs annoncés, forme historique.

    Conservée telle quelle pour les postes déployés, qui raisonnent encore par
    type. La liste détaillée vit dans `/api/auth/providers`.
    """
    types = []
    for config in usable_provider_configs():
        if config["provider_type"] not in types:
            types.append(config["provider_type"])
    types.append("legacy_email_code")
    return types


# ------------------------------------------------------------ OIDC générique
#
# ## Pourquoi cette section existe séparément
#
# Microsoft et Google ont des points de terminaison connus d'avance. Un IdP
# générique, non : c'est l'exploitant qui déclare un **émetteur**, et tout le
# reste — autorisation, jeton, clés — vient de son document de découverte.
#
# Cela introduit un risque que les deux autres fournisseurs n'avaient pas : une
# URL configurable fait émettre des requêtes au serveur. D'où le garde-fou
# ci-dessous, qui est la vraie nouveauté de cet adaptateur.

OIDC_METADATA_CACHE: dict[str, dict] = {}
OIDC_METADATA_TTL_SECONDS = 3600


class IssuerRejected(Exception):
    """Émetteur refusé par la politique d'URL. Porte un motif, jamais l'URL."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def issuer_url_is_allowed(issuer: str, allow_private: bool) -> tuple[bool, str]:
    """L'émetteur est-il une destination que ce serveur accepte de contacter ?

    Un émetteur configurable transforme le serveur en client HTTP dirigé par la
    configuration. Sans contrôle, cela permettrait d'atteindre le service de
    métadonnées d'un cloud (`169.254.169.254`), un port interne
    (`127.0.0.1:6379`) ou un voisin du réseau privé. C'est exactement le motif
    SSRF, et il ne se corrige pas après coup.

    Politique par défaut :

    - `https` obligatoire — un émetteur en clair n'authentifie rien ;
    - pas d'identifiants dans l'URL ;
    - pas de fragment ni de requête : un émetteur OIDC est une base ;
    - hôte résolvable et **publiquement routable**.

    Un Keycloak auto-hébergé sur un réseau privé reste possible, mais seulement
    si l'exploitant l'a explicitement autorisé côté serveur. Le poste ne peut
    pas activer ce mode : il ne le voit même pas.
    """
    try:
        parsed = urlparse(issuer)
    except ValueError:
        return False, "malformed"

    if parsed.scheme != "https":
        return False, "scheme_not_https"
    if parsed.username or parsed.password:
        return False, "credentials_in_url"
    if parsed.query or parsed.fragment:
        return False, "not_a_base_url"
    host = parsed.hostname
    if not host:
        return False, "missing_host"

    # Même politique réseau que la découverte — voir `host_is_publicly_routable`.
    # Un émetteur doit être joignable par ce serveur : un nom qu'il ne résout
    # pas est refusé, là où la découverte se contente d'annoncer une adresse.
    public, reason = host_is_publicly_routable(host, require_resolvable=True)
    if not public and not (allow_private and reason == "internal_address"):
        return False, reason
    return True, "ok"


async def fetch_oidc_metadata(issuer: str, allow_private: bool | None = None) -> dict:
    """Lit le document de découverte de l'émetteur.

    Les redirections ne sont **pas** suivies : une redirection vers une adresse
    interne contournerait le contrôle fait avant l'appel.
    """
    if allow_private is None:
        allow_private = OIDC_ALLOW_PRIVATE_ISSUER
    allowed, reason = issuer_url_is_allowed(issuer, allow_private)
    if not allowed:
        print(f"[auth] provider=oidc result=failure reason=issuer_rejected:{reason}")
        raise auth_error(400, "PROVIDER_CONFIG_INVALID")

    url = f"{issuer}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        raise auth_error(502, "OIDC_DISCOVERY_FAILED")
    if response.status_code != 200:
        raise auth_error(502, "OIDC_DISCOVERY_FAILED")
    try:
        metadata = response.json()
    except ValueError:
        raise auth_error(502, "OIDC_DISCOVERY_FAILED")

    # L'émetteur annoncé doit être **exactement** celui configuré. Une
    # comparaison permissive (préfixe, sous-chaîne, domaine) laisserait un
    # émetteur voisin se faire passer pour le bon.
    if (metadata.get("issuer") or "").rstrip("/") != issuer:
        raise auth_error(502, "OIDC_ISSUER_MISMATCH")

    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = metadata.get(field)
        if not value or not str(value).startswith("https://"):
            raise auth_error(502, "OIDC_DISCOVERY_FAILED")

    return metadata


async def oidc_metadata(
    issuer: str, force_refresh: bool = False, allow_private: bool | None = None
) -> dict:
    cached = OIDC_METADATA_CACHE.get(issuer)
    fresh = cached and cached["fetched_at"] + OIDC_METADATA_TTL_SECONDS > time.time()
    if cached and fresh and not force_refresh:
        return cached["metadata"]
    metadata = await fetch_oidc_metadata(issuer, allow_private)
    OIDC_METADATA_CACHE[issuer] = {"metadata": metadata, "fetched_at": time.time()}
    return metadata


async def provider_endpoints(config: dict) -> dict:
    """Points de terminaison effectifs d'une configuration.

    Statiques pour Microsoft et Google, découverts pour l'OIDC générique. Le
    reste du moteur n'a pas à savoir lequel des deux cas s'applique.
    """
    adapter = ADAPTERS[config["provider_type"]]
    if config["provider_type"] != "oidc":
        return {
            "authorization_endpoint": adapter["authorization_endpoint"],
            "token_endpoint": adapter["token_endpoint"],
        }
    metadata = await oidc_metadata(
        config["oidc_issuer"],
        allow_private=bool(config.get("oidc_allow_private_issuer")),
    )
    return {
        "authorization_endpoint": metadata["authorization_endpoint"],
        "token_endpoint": metadata["token_endpoint"],
    }


# ------------------------------------------------------------- bouclage


def is_loopback_redirect(redirect_uri: str) -> bool:
    """Le retour doit se faire sur la machine de l'utilisateur, et nulle part
    ailleurs.

    Sans cette vérification, un poste modifié pourrait faire échanger un code
    contre une URL de redirection qu'il contrôle. Seules les adresses de
    bouclage littérales sont acceptées — `localhost` est écarté parce qu'il
    dépend d'une résolution de noms que l'on ne maîtrise pas, et Google
    déconseille explicitement son usage pour cette raison.
    """
    try:
        parsed = urlparse(redirect_uri)
    except ValueError:
        return False
    if parsed.scheme != "http":
        return False
    if parsed.hostname not in {"127.0.0.1", "::1"}:
        return False
    return bool(parsed.port)


# ------------------------------------------------------------ validation


async def fetch_jwks(jwks_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(jwks_uri)
    if response.status_code != 200:
        raise auth_error(502, "JWKS_UNAVAILABLE")
    return response.json()


async def jwks_for_uri(jwks_uri: str, force_refresh: bool = False) -> dict:
    """JWKS mis en cache, rechargé à la demande.

    `force_refresh` sert au cas de la rotation : un `kid` absent du cache n'est
    pas une signature invalide, c'est peut-être une clé neuve.
    """
    cached = JWKS_CACHE.get(jwks_uri)
    fresh = cached and cached["fetched_at"] + JWKS_CACHE_TTL_SECONDS > time.time()
    if cached and fresh and not force_refresh:
        return cached["jwks"]
    jwks = await fetch_jwks(jwks_uri)
    JWKS_CACHE[jwks_uri] = {"jwks": jwks, "fetched_at": time.time()}
    return jwks


def signing_key_from_jwks(jwks: dict, kid: str):
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return jwt.PyJWK(key).key
    return None


async def validate_id_token(
    config: dict, id_token: str, expected_nonce_hash: str
) -> dict:
    """Valide complètement un jeton d'identité, quel que soit le fournisseur.

    Rien n'est cru avant cette fonction, et tout est vérifié : signature contre
    les clés publiques du fournisseur, algorithme, émetteur, audience,
    expiration, date d'entrée en vigueur et `nonce`. L'algorithme est **imposé**
    — accepter celui annoncé par le jeton laisserait passer `none` et les
    confusions HMAC/RSA.

    Le `kid` sert à choisir la clé, jamais l'URL : celle-ci vient de la
    configuration du fournisseur, pas d'un champ du jeton.
    """
    provider = config["provider_type"]
    adapter = ADAPTERS[provider]
    try:
        header = jwt.get_unverified_header(id_token)
    except Exception:
        raise auth_error(401, "ID_TOKEN_INVALID")

    if header.get("alg") not in adapter["algorithms"]:
        raise auth_error(401, "ID_TOKEN_INVALID")
    kid = header.get("kid")
    if not kid:
        raise auth_error(401, "ID_TOKEN_INVALID")

    # Revendications lues sans vérification, uniquement pour savoir *où*
    # chercher les clés et *quel* émetteur attendre. Elles sont ensuite
    # re-vérifiées par la signature et le contrôle d'émetteur : un tenant
    # falsifié ne peut pas produire une signature valide pour l'émetteur
    # correspondant.
    unverified = decode_jwt_claims(id_token)
    jwks_uri = adapter["jwks_uri"](unverified, config)
    expected_issuer = adapter["issuer"](unverified, config)
    if not jwks_uri or not expected_issuer.strip("/").endswith("v2.0") and provider == "microsoft_entra":
        # Chez Microsoft, un `tid` absent produit une URL et un émetteur
        # tronqués : on refuse avant même de contacter quoi que ce soit.
        raise auth_error(401, "ID_TOKEN_INVALID")

    jwks = await jwks_for_uri(jwks_uri)
    key = signing_key_from_jwks(jwks, kid)
    if key is None:
        # Clé inconnue : peut-être une rotation. On recharge une fois.
        jwks = await jwks_for_uri(jwks_uri, force_refresh=True)
        key = signing_key_from_jwks(jwks, kid)
    if key is None:
        raise auth_error(401, "ID_TOKEN_INVALID")

    try:
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=adapter["algorithms"],
            audience=config_client_id(config),
            issuer=expected_issuer,
            leeway=SSO_CLOCK_SKEW_SECONDS,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.InvalidTokenError:
        raise auth_error(401, "ID_TOKEN_INVALID")

    # Le `nonce` lie ce jeton à **cette** tentative de connexion. Sans lui, un
    # jeton valide capté ailleurs pourrait être rejoué ici.
    #
    # La comparaison porte sur les empreintes : le `nonce` n'a jamais besoin
    # d'être relu en clair, donc il n'a jamais besoin d'être stocké en clair.
    received = claims.get("nonce")
    if not expected_nonce_hash or not received:
        raise auth_error(401, "ID_TOKEN_INVALID")
    if not secrets.compare_digest(hash_token(str(received)), expected_nonce_hash):
        raise auth_error(401, "ID_TOKEN_INVALID")

    # Sujet immuable : `oid` chez Microsoft, `sub` chez Google.
    if not claims.get(adapter["subject_claim"]):
        raise auth_error(401, "ID_TOKEN_INVALID")

    return claims


async def validate_entra_id_token(id_token: str, expected_nonce: str) -> dict:
    """Conservé pour les tests et appels existants."""
    config = default_provider_config("microsoft_entra", nova_organization_id()) or {
        "provider_type": "microsoft_entra",
        "entra_tenant_id": ENTRA_ALLOWED_TENANT_ID,
    }
    return await validate_id_token(config, id_token, hash_token(expected_nonce))


def organization_for_tenant_legacy(provider: str, tenant_value: str) -> database.Row | None:
    """Organisation Nova rattachée à un tenant externe.

    Mapping **explicite** uniquement. Il n'existe pas de variante prenant un
    domaine d'adresse e-mail : rattacher une organisation depuis
    « @entreprise.com » donnerait son accès à quiconque contrôle une adresse
    dans ce domaine.

    Chez Google, la valeur rapprochée est la revendication `hd` — le domaine
    hébergé du Workspace. Ce n'est pas un identifiant immuable de client Google
    (celui-là n'est lisible que via l'Admin SDK, hors de nos portées), mais
    l'appariement reste **déclaré par un administrateur**, jamais déduit : un
    domaine inconnu de la table n'ouvre rien.
    """
    if not tenant_value:
        return None
    column = ADAPTERS[provider]["organization_column"]
    with db() as conn:
        return conn.execute(
            f"SELECT * FROM organizations WHERE {column}=?", (tenant_value,)
        ).fetchone()


def resolve_member_for_identity(
    email: str,
    organization_id: str,
    external_subject: str,
    provider: str,
    external_issuer: str = "",
) -> str:
    """Retrouve, rattache ou crée le compte correspondant à une identité
    **déjà vérifiée**.

    Quatre cas, dans cet ordre :

    1. **identité fédérée connue** — on retrouve le compte par le couple
       (fournisseur, sujet). C'est le chemin normal, et le seul qui ne repose
       sur aucune adresse ;
    2. **compte historique correspondant** — rattachement contrôlé. Il n'a lieu
       que si le tenant est vérifié, que l'organisation correspond, que
       l'adresse correspond, et qu'aucune identité fédérée **du même
       fournisseur** n'occupe déjà ce compte ;
    3. **compte déjà relié à un autre fournisseur** — le rattachement est
       autorisé, mais uniquement parce que l'organisation et l'adresse
       concordent dans un annuaire que l'organisation contrôle. Les deux
       identités coexistent alors sans fusionner : `(microsoft_entra, X)` et
       `(google_workspace, X)` restent deux lignes distinctes, et un sujet
       identique chez deux fournisseurs ne désigne jamais la même identité ;
    4. **nouveau membre autorisé** — création selon la politique actuelle, la
       même que pour le code e-mail.
    """
    with db() as conn:
        # Le périmètre d'une identité est (fournisseur, émetteur, sujet). Un
        # `sub` identique chez deux IdP OIDC ne désigne pas la même personne.
        existing = conn.execute(
            "SELECT email, organization_id FROM federated_identities "
            "WHERE organization_id=? AND provider=? AND external_issuer=? "
            "AND external_subject=?",
            (organization_id, provider, external_issuer, external_subject),
        ).fetchone()

        if existing:
            # Une identité rattachée à une autre organisation ne franchit pas
            # la frontière, même si le sujet correspond.
            if existing["organization_id"] != organization_id:
                raise auth_error(403, "ORGANIZATION_MISMATCH")
            return existing["email"]

        # Recherche **dans l'organisation du flux**. Chercher globalement
        # rattacherait une identité fédérée au compte homonyme d'un autre
        # tenant : deux personnes sans rapport, fusionnées en silence.
        user = user_by_email(conn, organization_id, email)

        if user:
            if user["organization_id"] and user["organization_id"] != organization_id:
                raise auth_error(403, "ORGANIZATION_MISMATCH")
            # Conflit uniquement à l'intérieur d'un même fournisseur : deux
            # identités Microsoft ne peuvent pas partager un compte, mais une
            # identité Google peut coexister avec une identité Microsoft.
            conflicting = conn.execute(
                "SELECT external_subject FROM federated_identities "
                "WHERE provider=? AND external_issuer=? AND organization_id=? "
                "AND lower(trim(email))=? AND external_subject<>?",
                (provider, external_issuer, organization_id,
                 normalize_email(email), external_subject),
            ).fetchone()
            if conflicting:
                raise auth_error(409, "IDENTITY_CONFLICT")
            return user["email"]

    # Nouveau membre : même politique d'admission que le code e-mail.
    #
    # Le compte naît **dans l'organisation du flux**. Qu'une autre organisation
    # compte déjà quelqu'un à cette adresse n'y change rien : ce sont deux
    # personnes, et c'est précisément ce que le modèle multi-tenant permet.
    if email.split("@")[-1] not in ALLOWED_DOMAINS:
        raise auth_error(403, "MEMBERSHIP_NOT_FOUND")
    touch_user(email, organization_id)
    return email


# -------------------------------------------------------------- endpoints


@app.get("/api/auth/providers")
def auth_providers():
    """Fournisseurs d'identité réellement utilisables sur ce serveur.

    Le libellé est de l'affichage : « Continue with OIDC » ne dirait rien à un
    utilisateur, « Continue with Company SSO » si. Il n'entre dans aucune
    décision de sécurité, et aucun secret ne figure ici.
    """
    configs = usable_provider_configs()
    providers = configured_providers()
    return {
        # Forme détaillée : ce que le poste dessine réellement.
        "provider_configs": [public_provider_view(config) for config in configs],
        # Formes historiques, conservées pour les postes déployés.
        "providers": providers,
        "display_names": {
            config["provider_type"]: config_display_name(config) for config in configs
        },
    }


@app.get("/api/auth/entra/pkce/available")
def entra_pkce_available():
    """Compatibilité : route historique interrogée par les postes déployés.

    Elle annonce désormais **tous** les fournisseurs configurés, pas seulement
    Microsoft ; `available` conserve son sens d'origine (« le SSO moderne
    est-il utilisable ? »).
    """
    providers = configured_providers()
    return {
        "available": any(p != "legacy_email_code" for p in providers),
        "providers": providers,
    }


async def start_sso_flow(
    provider: str,
    body: SsoStartRequest,
    purpose: str = FLOW_PURPOSE_LOGIN,
    expected_user_id: str | None = None,
    provider_type_override: str | None = None,
) -> dict:
    """Prépare une tentative et renvoie l'URL d'autorisation.

    Le serveur retient le `nonce` attendu : c'est lui qui le confrontera au
    jeton d'identité. Le `code_verifier`, lui, ne quitte jamais le poste avant
    l'échange — c'est tout l'intérêt de PKCE.
    """
    if not is_loopback_redirect(body.redirect_uri):
        raise auth_error(400, "REDIRECT_URI_INVALID")
    if len(body.code_challenge) < 43 or len(body.nonce) < 16:
        raise auth_error(400, "PKCE_INVALID")

    organization_id = nova_organization_id()
    # Le poste peut désigner une configuration précise ; sinon on prend la
    # première utilisable de ce type. Dans les deux cas, la configuration est
    # cherchée **dans l'organisation active** : un identifiant venu d'ailleurs
    # ne renvoie rien.
    if body.provider_config_id:
        config = provider_config_by_id(body.provider_config_id, organization_id)
        if config is None or not config["enabled"]:
            raise auth_error(404, "PROVIDER_CONFIG_NOT_FOUND")
        if provider and config["provider_type"] != provider:
            raise auth_error(400, "PROVIDER_CONFIG_MISMATCH")
        if validate_provider_config(config) is not None:
            raise auth_error(503, "PROVIDER_CONFIG_INVALID")
    else:
        if provider not in ADAPTERS:
            raise auth_error(404, "PROVIDER_UNKNOWN")
        config = default_provider_config(provider, organization_id)
        if config is None:
            raise auth_error(503, "PROVIDER_NOT_CONFIGURED")

    endpoints = await provider_endpoints(config)
    adapter = ADAPTERS[config["provider_type"]]

    # La tentative retient sa configuration, son type et son organisation : un
    # retour ne peut compléter ni un autre fournisseur, ni une autre
    # configuration du même fournisseur. Elle vit en base, donc l'échange peut
    # arriver sur un autre worker.
    flow_id = SSO_FLOW_STORE.create(
        provider_type=config["provider_type"],
        provider_config_id=config["id"],
        organization_id=organization_id,
        nonce=body.nonce,
        redirect_uri=body.redirect_uri,
        machine=body.machine,
        purpose=purpose,
        expected_user_id=expected_user_id,
    )

    parameters = {
        "client_id": config_client_id(config),
        "response_type": "code",
        "redirect_uri": body.redirect_uri,
        "scope": config_scopes(config),
        "code_challenge": body.code_challenge,
        "code_challenge_method": "S256",
        "nonce": body.nonce,
        **adapter["extra_authorize_params"],
    }
    if config["provider_type"] == "microsoft_entra":
        parameters["response_mode"] = "query"

    if purpose == FLOW_PURPOSE_ADMIN_STEP_UP:
        # `max_age` est le paramètre qui compte, et c'est un paramètre du
        # standard OpenID Connect : il oblige l'OP à réauthentifier si la
        # dernière authentification est plus ancienne, **et** à renvoyer
        # `auth_time`. C'est cette obligation qui rend la fraîcheur vérifiable ;
        # `prompt=login` seul demanderait une interaction sans rien prouver.
        parameters["max_age"] = ADMIN_STEP_UP_MAX_AGE_SECONDS
        # `prompt=login` en complément : Microsoft comme Google le documentent,
        # et il évite qu'un OP interprète `max_age` avec largesse. Les deux
        # ensemble demandent l'interaction *et* rendent la fraîcheur prouvable —
        # mais c'est `auth_time` que le serveur vérifie, jamais la présence du
        # paramètre, qu'un IdP hostile pourrait ignorer.
        parameters["prompt"] = "login"

    return {
        "flow_id": flow_id,
        "provider_config_id": config["id"],
        "authorization_url": f"{endpoints['authorization_endpoint']}?{urlencode(parameters)}",
        "expires_in": SSO_FLOW_TTL_SECONDS,
    }


async def redeem_authorization_code(config: dict, body: SsoExchangeRequest) -> dict:
    """Échange le code d'autorisation contre les jetons du fournisseur.

    Ce qui prouve que le code revient à celui qui l'a demandé, c'est le
    `code_verifier`. Google exige en plus le secret de son client « Desktop » ;
    il ne quitte pas le serveur, et le poste n'en a jamais connaissance.
    """
    endpoints = await provider_endpoints(config)
    data = {
        "client_id": config_client_id(config),
        "grant_type": "authorization_code",
        "code": body.code,
        "redirect_uri": body.redirect_uri,
        "code_verifier": body.code_verifier,
    }
    auth = None
    if config_requires_secret(config):
        # `client_secret_basic` place l'identifiant dans l'en-tête plutôt que
        # dans le corps : les deux méthodes sont standard, l'IdP choisit.
        if (
            config["provider_type"] == "oidc"
            and config.get("oidc_client_auth_method") == "client_secret_basic"
        ):
            auth = (config_client_id(config), config_client_secret(config))
        else:
            data["client_secret"] = config_client_secret(config)
    else:
        data["scope"] = config_scopes(config)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            endpoints["token_endpoint"], data=data, auth=auth
        )
    if response.status_code != 200:
        # Le détail du fournisseur (`invalid_grant`, code expiré, code déjà
        # utilisé) n'est pas renvoyé au poste : il ne changerait pas sa conduite
        # et décrirait l'état d'un secret.
        raise auth_error(400, "TOKEN_EXCHANGE_FAILED")
    return response.json()


async def exchange_sso_code(provider: str, body: SsoExchangeRequest) -> dict:
    """Transforme un code d'autorisation en session Nova.

    Le jeton du fournisseur ne ressort pas d'ici : le poste ne reçoit qu'une
    session Nova opaque, avec son propre cycle de vie. Aucun jeton de
    rafraîchissement externe n'est conservé — le fournisseur a prouvé
    l'identité, son rôle s'arrête là.
    """
    # Réservation **atomique** : deux workers présentant le même identifiant au
    # même instant, un seul passe. C'est ce qui empêche deux échanges avec le
    # même code d'autorisation.
    flow = SSO_FLOW_STORE.begin_exchange(body.flow_id)
    if flow is None:
        # Inconnue, expirée, déjà en cours d'échange ou déjà consommée : du
        # point de vue de l'appelant, la tentative n'est plus disponible.
        raise auth_error(400, "AUTH_TIMEOUT")
    # Une tentative appartient à son fournisseur **et** à sa configuration : ni
    # un retour Google ne satisfait une tentative Microsoft, ni un retour d'un
    # IdP OIDC n'en satisfait un autre.
    if provider and flow["provider_type"] != provider:
        raise auth_error(400, "STATE_MISMATCH")
    if flow["organization_id"] != nova_organization_id():
        raise auth_error(400, "STATE_MISMATCH")
    if flow["redirect_uri"] != body.redirect_uri:
        raise auth_error(400, "REDIRECT_URI_INVALID")
    # Le but fait partie de la tentative. Une réauthentification
    # d'administration présentée à la route de connexion ordinaire ouvrirait une
    # session utilisateur — ce n'est pas ce qu'elle demandait, et la tentative
    # serait consommée pour rien.
    if (flow["purpose"] or FLOW_PURPOSE_LOGIN) != FLOW_PURPOSE_LOGIN:
        raise auth_error(400, "FLOW_PURPOSE_MISMATCH")

    config = provider_config_by_id(flow["provider_config_id"], flow["organization_id"])
    if config is None or not config["enabled"]:
        raise auth_error(503, "PROVIDER_NOT_CONFIGURED")
    adapter = ADAPTERS[config["provider_type"]]

    tokens = await redeem_authorization_code(config, body)
    id_token = tokens.get("id_token")
    if not id_token:
        raise auth_error(401, "ID_TOKEN_INVALID")

    claims = await validate_id_token(config, id_token, flow["nonce_hash"])
    external_subject = claims[adapter["subject_claim"]]

    if config["provider_type"] == "oidc":
        # Pas de devinette de tenant : la configuration OIDC **appartient** à
        # une organisation, et l'émetteur vérifié est ce qui la désigne.
        external_issuer = config["oidc_issuer"]
        claimed_tenant = external_issuer
        required_claim = config.get("oidc_required_claim")
        if required_claim:
            expected = config.get("oidc_required_claim_value") or ""
            if str(claims.get(required_claim, "")) != expected:
                raise auth_error(403, "MEMBERSHIP_NOT_FOUND")
    else:
        external_issuer = ""
        claimed_tenant = claims.get(adapter["tenant_claim"], "")

    # Le rattachement vient de la configuration, pas d'une recherche globale :
    # la valeur portée par le jeton doit être exactement celle que
    # l'organisation a déclarée. Un compte Google personnel, qui ne porte aucun
    # `hd`, ne correspond à rien et n'entre nulle part.
    expected_tenant = config_tenant_value(config)
    if not claimed_tenant or claimed_tenant != expected_tenant:
        raise auth_error(403, "TENANT_NOT_ALLOWED")
    tenant_value = claimed_tenant
    with db() as conn:
        organization = conn.execute(
            "SELECT * FROM organizations WHERE id=?", (config["organization_id"],)
        ).fetchone()
    if organization is None or organization["id"] != nova_organization_id():
        raise auth_error(403, "TENANT_NOT_ALLOWED")

    email = (claims.get("preferred_username") or claims.get("email") or "").lower()
    if not email:
        raise auth_error(401, "ID_TOKEN_INVALID")

    resolved_email = resolve_member_for_identity(
        email,
        organization["id"],
        external_subject,
        config["provider_type"],
        external_issuer,
    )

    with db() as conn:
        account = user_by_email(conn, organization["id"], resolved_email)
    if account and (account["disabled"] or (account["status"] or "active") != "active"):
        raise auth_error(403, "ACCOUNT_DISABLED")

    session = issue_token(
        resolved_email, flow["machine"] or "poste inconnu", organization["id"]
    )
    # La tentative n'est marquée consommée qu'une fois la session émise : avant
    # cela, elle est « en cours d'échange », donc déjà inutilisable par un autre
    # worker, mais l'état dit ce qui s'est réellement passé.
    SSO_FLOW_STORE.mark_consumed(body.flow_id)
    # Identité désormais **vérifiée** : signature, émetteur, audience,
    # expiration, nonce et rattachement l'ont été. C'est ce qui la distingue du
    # Device Code hérité, qui reste `transport_only`.
    record_federated_identity(
        resolved_email,
        config["provider_type"],
        external_subject,
        tenant_value or None,
        verification="verified",
        external_issuer=external_issuer,
        organization_id=organization["id"],
    )
    print(
        f"[auth] provider={config['provider_type']} config={config['id'][:8]} "
        f"organization={organization['id'][:8]} result=success"
    )
    return {"status": "complete", "email": resolved_email, **session}


@app.post("/api/auth/sso/{provider}/start")
async def sso_start(provider: str, body: SsoStartRequest):
    return await start_sso_flow(provider, body)


@app.post("/api/auth/sso/{provider}/exchange")
async def sso_exchange(provider: str, body: SsoExchangeRequest):
    return await exchange_sso_code(provider, body)


# --- routes historiques Microsoft, conservées telles quelles ---------------


@app.post("/api/auth/entra/pkce/start")
async def entra_pkce_start(body: SsoStartRequest):
    return await start_sso_flow("microsoft_entra", body)


@app.post("/api/auth/entra/pkce/exchange")
async def entra_pkce_exchange(body: SsoExchangeRequest):
    return await exchange_sso_code("microsoft_entra", body)




# ----------------------------- administration des configurations (interne)
#
# Ces routes servent Nova Admin et exigent une session d'administration, comme
# le reste. Leur préfixe `/api/admin` est historique : il ne désigne plus un
# mode d'authentification, seulement l'endroit où elles sont nées. La portée
# reste l'organisation servie par cette instance, jamais une autre.


class ProviderConfigRequest(BaseModel):
    provider_type: str
    display_name: str | None = None
    enabled: bool = True
    entra_tenant_id: str | None = None
    google_hosted_domain: str | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    # Vide ou absent à la mise à jour = « ne change pas ». Effacer un secret
    # demande un geste explicite, pas un champ oublié.
    oidc_client_secret: str | None = None
    oidc_client_auth_method: str | None = None
    oidc_allow_private_issuer: bool | None = None
    oidc_required_claim: str | None = None
    oidc_required_claim_value: str | None = None


def admin_provider_view(config: dict) -> dict:
    """Vue d'administration.

    Elle montre l'état réel — désactivé, invalide et pourquoi — mais **jamais**
    un secret : `has_secret` dit qu'il existe, pas ce qu'il vaut. Un secret
    qu'une API peut relire finit par être relu.
    """
    return {
        "id": config["id"],
        "organization_id": config["organization_id"],
        "provider_type": config["provider_type"],
        "display_name": config_display_name(config),
        "enabled": bool(config["enabled"]),
        "entra_tenant_id": config.get("entra_tenant_id"),
        "google_hosted_domain": config.get("google_hosted_domain"),
        "oidc_issuer": config.get("oidc_issuer"),
        "oidc_client_id": config.get("oidc_client_id"),
        "oidc_client_auth_method": config.get("oidc_client_auth_method"),
        "oidc_allow_private_issuer": bool(config.get("oidc_allow_private_issuer")),
        "oidc_required_claim": config.get("oidc_required_claim"),
        "has_secret": bool(config.get("oidc_client_secret")),
        "validation_error": validate_provider_config(config),
        "created_at": config.get("created_at"),
        "updated_at": config.get("updated_at"),
    }


@app.get("/api/admin/provider-configs")
def admin_list_provider_configs(
    _: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    """Toutes les configurations de l'organisation active, cassées comprises."""
    configs = provider_configs_for_organization(
        nova_organization_id(), only_usable=False
    )
    return {"configs": [admin_provider_view(config) for config in configs]}


@app.post("/api/admin/provider-configs")
def admin_create_provider_config(
    body: ProviderConfigRequest,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_PROVIDER_MANAGE)),
):
    if body.provider_type not in PROVIDER_TYPES:
        raise auth_error(400, "PROVIDER_UNKNOWN")
    now = time.time()
    config_id = str(uuid.uuid4())
    fields = {
        "id": config_id,
        "organization_id": nova_organization_id(),
        "provider_type": body.provider_type,
        "display_name": body.display_name,
        "enabled": 1 if body.enabled else 0,
        "entra_tenant_id": body.entra_tenant_id,
        "google_hosted_domain": body.google_hosted_domain,
        "oidc_issuer": (body.oidc_issuer or "").rstrip("/") or None,
        "oidc_client_id": body.oidc_client_id,
        "oidc_client_secret": body.oidc_client_secret or None,
        "oidc_client_auth_method": body.oidc_client_auth_method or "none",
        "oidc_allow_private_issuer": 1 if body.oidc_allow_private_issuer else 0,
        "oidc_required_claim": body.oidc_required_claim,
        "oidc_required_claim_value": body.oidc_required_claim_value,
        "created_at": now,
        "updated_at": now,
    }
    names = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    with db() as conn:
        try:
            conn.execute(
                f"INSERT INTO organization_provider_configs({names}) "
                f"VALUES ({placeholders})",
                tuple(fields.values()),
            )
        except database.IntegrityError:
            raise auth_error(409, "PROVIDER_CONFIG_DUPLICATE")
    created = provider_config_by_id(config_id, nova_organization_id())
    # Le type et l'existence d'un secret — jamais le secret lui-même.
    # « Admin » ne veut pas dire « voir les secrets », et un journal d'audit
    # est exactement l'endroit où un secret survivrait le plus longtemps.
    record_admin_action(
        principal,
        "provider_config.create",
        target_type="provider_config",
        target_id=config_id,
        metadata={
            "provider_type": body.provider_type,
            "has_secret": bool(body.oidc_client_secret),
        },
    )
    return admin_provider_view(created)


@app.post("/api/admin/provider-configs/{config_id}")
def admin_update_provider_config(
    config_id: str,
    body: ProviderConfigRequest,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_PROVIDER_MANAGE)),
):
    organization_id = nova_organization_id()
    existing = provider_config_by_id(config_id, organization_id)
    if existing is None:
        raise auth_error(404, "PROVIDER_CONFIG_NOT_FOUND")

    updates = {
        "display_name": body.display_name,
        "enabled": 1 if body.enabled else 0,
        "entra_tenant_id": body.entra_tenant_id,
        "google_hosted_domain": body.google_hosted_domain,
        "oidc_issuer": (body.oidc_issuer or "").rstrip("/") or None,
        "oidc_client_id": body.oidc_client_id,
        "oidc_client_auth_method": body.oidc_client_auth_method or "none",
        "oidc_allow_private_issuer": 1 if body.oidc_allow_private_issuer else 0,
        "oidc_required_claim": body.oidc_required_claim,
        "oidc_required_claim_value": body.oidc_required_claim_value,
        "updated_at": time.time(),
    }
    # Un secret absent du corps **conserve** l'ancien. Sans cette règle, une
    # mise à jour de libellé effacerait silencieusement le secret et couperait
    # la connexion de toute une organisation.
    if body.oidc_client_secret:
        updates["oidc_client_secret"] = body.oidc_client_secret

    assignments = ", ".join(f"{name}=?" for name in updates)
    with db() as conn:
        conn.execute(
            f"UPDATE organization_provider_configs SET {assignments} "
            f"WHERE id=? AND organization_id=?",
            (*updates.values(), config_id, organization_id),
        )
    record_admin_action(
        principal,
        "provider_config.update",
        target_type="provider_config",
        target_id=config_id,
        metadata={
            "fields": sorted(updates),
            # Que le secret ait été remplacé est un fait d'audit ; sa valeur,
            # ni l'ancienne ni la nouvelle, n'en est un.
            "secret_replaced": bool(body.oidc_client_secret),
        },
    )
    return admin_provider_view(provider_config_by_id(config_id, organization_id))


@app.post("/api/admin/provider-configs/{config_id}/disable")
def admin_disable_provider_config(
    config_id: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_PROVIDER_MANAGE)),
):
    """Désactive plutôt que supprime.

    Une configuration désactivée cesse d'être annoncée et de servir, mais reste
    lisible : les identités fédérées qu'elle a créées gardent ainsi leur
    contexte, et un retour arrière ne demande pas de tout ressaisir.
    """
    organization_id = nova_organization_id()
    if provider_config_by_id(config_id, organization_id) is None:
        raise auth_error(404, "PROVIDER_CONFIG_NOT_FOUND")

    # Garde-fou d'auto-verrouillage.
    #
    # Depuis la Phase 24, ouvrir une session d'administration exige une
    # réauthentification auprès d'un fournisseur. Désactiver le dernier
    # fournisseur actif ferme donc la porte à **tous** les administrateurs, y
    # compris à celui qui vient de cliquer. Le jeton hérité offre bien un
    # chemin de secours en mode dédié, mais s'y fier serait fragile : il peut
    # être éteint ensuite, et il est destiné à disparaître.
    #
    # Le refus n'empêche pas de remplacer un fournisseur : il suffit de
    # déclarer le nouveau avant de désactiver l'ancien.
    with db() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM organization_provider_configs "
            "WHERE organization_id=? AND enabled=1 AND id<>?",
            (organization_id, config_id),
        ).fetchone()["c"]
    if remaining == 0:
        raise auth_error(409, "PROVIDER_LAST_ACTIVE")

    with db() as conn:
        conn.execute(
            "UPDATE organization_provider_configs SET enabled=0, updated_at=? "
            "WHERE id=? AND organization_id=?",
            (time.time(), config_id, organization_id),
        )
    record_admin_action(
        principal,
        "provider_config.disable",
        target_type="provider_config",
        target_id=config_id,
    )
    return admin_provider_view(provider_config_by_id(config_id, organization_id))


@app.post("/api/discovery/organization")
def discover_organization(body: DiscoveryRequest):
    """Résout une organisation en adresse de service.

    ## Anti-énumération
    #
    Toutes les issues négatives — organisation inconnue, découverte désactivée,
    organisation suspendue, adresse de service absente ou invalide — produisent
    **la même réponse** : `404 ORGANIZATION_NOT_AVAILABLE`.
    #
    Distinguer « n'existe pas » de « suspendue » transformerait cet endpoint en
    oracle : on y testerait des noms d'entreprises pour savoir lesquelles sont
    clientes de Nova, et lesquelles ont cessé de payer. Ce n'est l'affaire de
    personne.
    """
    organization = DISCOVERY_RESOLVER.resolve(body.organization)
    bootstrap = discovery_bootstrap_for(organization) if organization else None
    if bootstrap is None:
        # Le journal ne porte pas l'identifiant demandé : l'endpoint est public,
        # et accumuler les recherches infructueuses reviendrait à constituer la
        # liste des organisations que l'on cherche à joindre.
        print("[discovery] result=not_available")
        raise auth_error(404, "ORGANIZATION_NOT_AVAILABLE")
    print(
        f"[discovery] result=ok mode={bootstrap['deployment_mode']} "
        f"version={DISCOVERY_CONTRACT_VERSION}"
    )
    return bootstrap


@app.get("/api/admin/discovery")
def admin_read_discovery(
    _: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM organizations WHERE id=?", (nova_organization_id(),)
        ).fetchone()
    organization = dict(row) if row else {}
    endpoint = organization.get("service_endpoint") or ""
    allowed, reason = service_endpoint_is_allowed(
        endpoint, DISCOVERY_ALLOW_INSECURE_ENDPOINT
    )
    return {
        "discovery_slug": organization.get("discovery_slug"),
        "discovery_enabled": bool(organization.get("discovery_enabled")),
        "deployment_mode": organization.get("deployment_mode"),
        "service_endpoint": endpoint,
        "status": organization.get("status"),
        "service_endpoint_error": None if allowed else reason,
    }


class DiscoverySettings(BaseModel):
    discovery_slug: str | None = None
    discovery_enabled: bool | None = None
    service_endpoint: str | None = None
    status: str | None = None


@app.post("/api/admin/discovery")
def admin_update_discovery(
    body: DiscoverySettings,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_DISCOVERY_MANAGE)),
):
    """Modifie la configuration de découverte de l'organisation active.

    Le slug est **immuable une fois la découverte activée**. Le changer
    invaliderait d'un coup le code que portent tous les postes déjà déployés, et
    aucun mécanisme d'alias n'existe pour amortir cela. Le désactiver d'abord
    rend le changement possible — et explicite.
    """
    organization_id = nova_organization_id()
    with db() as conn:
        current = conn.execute(
            "SELECT * FROM organizations WHERE id=?", (organization_id,)
        ).fetchone()
    current = dict(current) if current else {}

    updates = {}
    if body.discovery_slug is not None:
        wanted = normalize_discovery_slug(body.discovery_slug)
        if (
            current.get("discovery_enabled")
            and normalize_discovery_slug(current.get("discovery_slug") or "") != wanted
        ):
            raise auth_error(409, "DISCOVERY_SLUG_IMMUTABLE")
        updates["discovery_slug"] = wanted
    if body.discovery_enabled is not None:
        updates["discovery_enabled"] = 1 if body.discovery_enabled else 0
    if body.service_endpoint is not None:
        endpoint = body.service_endpoint.strip().rstrip("/")
        allowed, reason = service_endpoint_is_allowed(
            endpoint, DISCOVERY_ALLOW_INSECURE_ENDPOINT
        )
        if endpoint and not allowed:
            print(f"[discovery] result=failure reason=endpoint_rejected:{reason}")
            raise auth_error(400, "SERVICE_ENDPOINT_INVALID")
        updates["service_endpoint"] = endpoint or None
    if body.status is not None:
        if body.status not in {"active", "suspended", "disabled"}:
            raise auth_error(400, "ORGANIZATION_STATUS_INVALID")
        updates["status"] = body.status

    if updates:
        assignments = ", ".join(f"{name}=?" for name in updates)
        with db() as conn:
            conn.execute(
                f"UPDATE organizations SET {assignments} WHERE id=?",
                (*updates.values(), organization_id),
            )
        # Les *noms* des champs modifiés, pas leurs valeurs : savoir que
        # l'adresse de service a changé suffit à l'audit.
        record_admin_action(
            principal,
            "discovery.update",
            target_type="organization",
            target_id=organization_id,
            metadata={"fields": sorted(updates)},
        )
    return admin_read_discovery(principal)


# ───────────────────── Session d'administration et Control Plane ──────────
#
# Séparation des routes : `/api/discovery/*`, `/api/auth/*` et `/api/me` sont
# publiques ou destinées au poste. `/api/admin/*` et `/api/control/*` exigent un
# principal d'administration. Aucune route d'administration ne se cache dans les
# chemins publics historiques.


class AdminStepUpStartRequest(BaseModel):
    """Ouverture d'une réauthentification. Mêmes paramètres qu'une connexion.

    Le client ne choisit **ni** le fournisseur **ni** l'utilisateur : le serveur
    les déduit de la session présentée et de l'identité fédérée existante.
    """

    code_challenge: str
    nonce: str
    redirect_uri: str
    machine: str = "poste inconnu"


class AdminStepUpCompleteRequest(BaseModel):
    flow_id: str
    code: str
    code_verifier: str
    redirect_uri: str


def admin_candidate(email: str):
    """Le compte, s'il peut prétendre à une session d'administration.

    Rôle, statut du compte et statut de l'organisation sont vérifiés ici **et**
    de nouveau à la fin du parcours : entre les deux, un administrateur peut
    avoir été révoqué, et une réauthentification réussie ne doit pas rattraper
    un privilège retiré entre-temps.
    """
    organization_id = nova_organization_id()
    with db() as conn:
        row = user_by_email(conn, organization_id, email)
        organization = conn.execute(
            "SELECT status FROM organizations WHERE id=?", (organization_id,)
        ).fetchone()
    # Un seul et même refus, quelle qu'en soit la raison : « ce compte existe
    # mais n'est pas administrateur » n'est une information utile qu'à celui qui
    # sonde.
    if row is None or security_role_for(row) not in ADMIN_SECURITY_ROLES:
        raise auth_error(403, "ADMIN_ROLE_REQUIRED")
    if account_status(row) != "active":
        raise auth_error(403, "ADMIN_ROLE_REQUIRED")
    if organization is not None and (organization["status"] or "active") != "active":
        raise auth_error(403, "ADMIN_ROLE_REQUIRED")
    return row


def step_up_identity_for(user_row):
    """L'identité fédérée que la réauthentification devra reprouver.

    Le fournisseur n'est **pas** choisi par le client : il est déterminé par
    l'identité vérifiée que ce compte possède déjà. Laisser le client le choisir
    permettrait de contourner l'IdP réel de l'organisation en désignant un
    fournisseur plus complaisant.

    Seules les identités `verified` comptent. Le Device Code hérité produit du
    `transport_only` — des revendications lues sans vérification de signature —
    et ne prouve donc rien ; une session ouverte ainsi devra passer par le
    parcours moderne du même fournisseur, s'il en existe une trace vérifiée.
    """
    with db() as conn:
        return conn.execute(
            "SELECT * FROM federated_identities WHERE user_id=? AND organization_id=? "
            "AND verification='verified' ORDER BY last_seen DESC LIMIT 1",
            (user_row["user_id"], user_row["organization_id"]),
        ).fetchone()


@app.post("/api/admin/session")
def open_admin_session(user: str = Depends(current_user)):
    """Cette route n'émet plus de session : elle indique le chemin à suivre.

    Elle existait pour ouvrir une session d'administration à partir d'une session
    utilisateur seule. C'était précisément le défaut : une session utilisateur
    peut avoir trois semaines, avoir été copiée d'un poste, ou volée — elle prouve
    qu'une authentification a eu lieu, pas qu'une personne est là.

    Elle est conservée plutôt que supprimée pour que la réponse soit explicite
    plutôt qu'un `404` déroutant. Elle vérifie toujours le rôle : inutile
    d'expliquer le parcours de réauthentification à qui n'y a pas droit.
    """
    admin_candidate(user)
    raise auth_error(403, "ADMIN_STEP_UP_REQUIRED")


@app.post("/api/admin/step-up/start")
async def admin_step_up_start(
    body: AdminStepUpStartRequest, user: str = Depends(current_user)
):
    """Ouvre une réauthentification auprès de l'IdP de l'organisation.

    Ce que cette route accorde à qui présente une session utilisateur volée :
    **le droit de commencer**. Rien d'autre. La preuve devra venir de l'IdP, être
    récente, et résoudre exactement le même compte — trois conditions qu'un jeton
    volé ne procure pas.
    """
    enforce_rate_limit("principal", hash_token(user), ratelimit.ADMIN_STEP_UP)
    account = admin_candidate(user)
    identity = step_up_identity_for(account)
    if identity is None:
        # Compte sans identité fédérée vérifiée : code e-mail hérité, ou Device
        # Code. Voir la docstring de `step_up_identity_for`.
        print("[admin] step_up result=failure reason=unsupported_identity")
        raise auth_error(400, "ADMIN_STEP_UP_UNSUPPORTED")

    started = await start_sso_flow(
        identity["provider"],
        SsoStartRequest(
            code_challenge=body.code_challenge,
            nonce=body.nonce,
            redirect_uri=body.redirect_uri,
            machine=body.machine,
        ),
        purpose=FLOW_PURPOSE_ADMIN_STEP_UP,
        expected_user_id=account["user_id"],
    )
    print(
        f"[admin] step_up start provider={identity['provider']} "
        f"user={account['user_id'][:8]}"
    )
    return {
        "flow_id": started["flow_id"],
        "authorization_url": started["authorization_url"],
        "expires_in": started["expires_in"],
    }


@app.post("/api/admin/step-up/complete")
async def admin_step_up_complete(
    body: AdminStepUpCompleteRequest, user: str = Depends(current_user)
):
    """Vérifie la réauthentification, puis ouvre la session d'administration.

    Aucun jeton intermédiaire n'est émis entre la preuve et la session : un
    secret de plus serait un secret de plus à voler, sans rien apporter puisque
    la session peut être créée ici même.

    Ce que cette route **ne fait jamais** : créer un compte, créer une identité
    fédérée, rattacher une identité à un compte. Une réauthentification reprouve
    une identité existante — elle n'en établit pas de nouvelle. Un rattachement
    silencieux pendant une élévation de privilège serait le pire endroit
    possible pour en faire un.
    """
    account = admin_candidate(user)

    # Réservation atomique, comme une connexion ordinaire : une preuve ne sert
    # qu'une fois, et un second retour avec le même identifiant ne passe pas.
    flow = SSO_FLOW_STORE.begin_exchange(body.flow_id)
    if flow is None:
        raise auth_error(400, "AUTH_TIMEOUT")
    if (flow["purpose"] or FLOW_PURPOSE_LOGIN) != FLOW_PURPOSE_ADMIN_STEP_UP:
        # Un retour de connexion ordinaire n'est pas une preuve d'administration.
        raise auth_error(400, "FLOW_PURPOSE_MISMATCH")
    if flow["organization_id"] != nova_organization_id():
        raise auth_error(400, "STATE_MISMATCH")
    if flow["redirect_uri"] != body.redirect_uri:
        raise auth_error(400, "REDIRECT_URI_INVALID")
    if flow["expected_user_id"] != account["user_id"]:
        # La tentative a été ouverte par quelqu'un d'autre.
        raise auth_error(403, "ADMIN_STEP_UP_IDENTITY_MISMATCH")

    config = provider_config_by_id(flow["provider_config_id"], flow["organization_id"])
    if config is None or not config["enabled"]:
        # Configuration désactivée entre le début et la fin du parcours.
        raise auth_error(503, "PROVIDER_NOT_CONFIGURED")
    adapter = ADAPTERS[config["provider_type"]]

    tokens = await redeem_authorization_code(
        config,
        SsoExchangeRequest(
            flow_id=body.flow_id, code=body.code,
            code_verifier=body.code_verifier, redirect_uri=body.redirect_uri,
        ),
    )
    id_token = tokens.get("id_token")
    if not id_token:
        raise auth_error(401, "ID_TOKEN_INVALID")
    claims = await validate_id_token(config, id_token, flow["nonce_hash"])

    # ── Fraîcheur ──────────────────────────────────────────────────────
    #
    # Le contrôle central de cette phase. « Le jeton vient d'être émis » ne dit
    # rien : un IdP peut le réémettre depuis une session vieille de plusieurs
    # jours, sans rien demander à personne. Seul `auth_time` dit quand la
    # personne s'est réellement authentifiée.
    fresh, reason = authentication_is_fresh(claims.get("auth_time"))
    if not fresh:
        print(f"[admin] step_up result=failure reason={reason}")
        raise auth_error(401, "ADMIN_STEP_UP_TOO_OLD")

    # ── Même identité ──────────────────────────────────────────────────
    #
    # Le navigateur peut être connecté à un autre compte de la même
    # organisation. Même domaine, même adresse même : rien de tout cela ne
    # suffit. C'est l'identité fédérée qui doit résoudre le même `user_id`.
    external_subject = claims.get(adapter["subject_claim"])
    external_issuer = config["oidc_issuer"] if config["provider_type"] == "oidc" else ""
    if not external_subject:
        raise auth_error(401, "ID_TOKEN_INVALID")
    with db() as conn:
        identity = conn.execute(
            "SELECT user_id, organization_id FROM federated_identities "
            "WHERE provider=? AND external_issuer=? AND external_subject=?",
            (config["provider_type"], external_issuer, external_subject),
        ).fetchone()
    if (
        identity is None
        or identity["user_id"] != account["user_id"]
        or identity["organization_id"] != flow["organization_id"]
    ):
        # Aucun rattachement de secours : si l'identité est inconnue, elle le
        # reste. Une élévation de privilège n'est pas le moment de lier un
        # compte.
        print("[admin] step_up result=failure reason=identity_mismatch")
        raise auth_error(403, "ADMIN_STEP_UP_IDENTITY_MISMATCH")

    # Le tenant reste vérifié comme pour une connexion : une preuve venue d'un
    # tenant non déclaré ne vaut rien, même si le sujet correspond.
    if config["provider_type"] != "oidc":
        claimed_tenant = claims.get(adapter["tenant_claim"], "")
        if not claimed_tenant or claimed_tenant != config_tenant_value(config):
            raise auth_error(403, "TENANT_NOT_ALLOWED")

    # ── Le rôle, une dernière fois ─────────────────────────────────────
    #
    # Relu maintenant, et non au début : entre les deux, il a pu être retiré. Une
    # réauthentification réussie ne rattrape pas un privilège révoqué.
    account = admin_candidate(user)

    SSO_FLOW_STORE.mark_consumed(body.flow_id)
    session = create_admin_session(account, config["provider_type"])
    principal = AdminPrincipal(
        user_id=account["user_id"],
        email=account["email"],
        organization_id=account["organization_id"] or nova_organization_id(),
        security_role=session["role"],
        session_id=session["session_id"],
        auth_provider=config["provider_type"],
        issued_at=time.time(),
    )
    record_admin_action(
        principal, "admin_step_up.success", target_type="admin_session",
        target_id=session["session_id"],
        metadata={"provider_type": config["provider_type"]},
    )
    record_admin_action(
        principal, "admin_session.create", target_type="admin_session",
        target_id=session["session_id"],
    )
    # Ni jeton, ni `auth_time`, ni adresse : le fournisseur et l'issue suffisent.
    print(f"[admin] step_up result=success provider={config['provider_type']}")
    return {
        "token": session["token"],
        "role": session["role"],
        "organization_id": principal.organization_id,
        "capabilities": sorted(capabilities_for_role(session["role"])),
        "expires_in": int(session["expires_at"] - time.time()),
    }


@app.post("/api/admin/session/logout")
def close_admin_session(principal: AdminPrincipal = Depends(admin_principal)):
    """Ferme la session d'administration courante."""
    if principal.session_id is None:
        # Le jeton hérité n'a pas de session à fermer : le dire est plus honnête
        # qu'un succès sans effet.
        raise auth_error(400, "ADMIN_SESSION_NOT_APPLICABLE")
    with db() as conn:
        conn.execute(
            "UPDATE admin_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (time.time(), principal.session_id),
        )
    return {"revoked": True}


def seats_in_use(organization_id: str) -> int:
    """Personnes ayant au moins une session active dans l'organisation.

    Compte des `user_id` distincts, et non des sessions : quelqu'un avec trois
    postes occupe un siege, pas trois.
    """
    now = time.time()
    with db() as conn:
        return conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM tokens WHERE active=1 "
            "AND organization_id=? AND (expires_at IS NULL OR expires_at>?)",
            (organization_id, now),
        ).fetchone()["c"]


def control_organization_view(organization_id: str) -> dict:
    """Vue d'administration d'une organisation. Des résumés, jamais de secrets."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM organizations WHERE id=?", (organization_id,)
        ).fetchone()
        configs = conn.execute(
            "SELECT provider_type, enabled FROM organization_provider_configs "
            "WHERE organization_id=?",
            (organization_id,),
        ).fetchall()
    if row is None:
        raise auth_error(404, "ORGANIZATION_NOT_FOUND")
    return {
        "organization": {
            "id": row["id"],
            "slug": row["slug"],
            "display_name": row["display_name"],
            "org_type": row["org_type"],
            "status": row["status"] or "active",
        },
        "deployment": {
            "server_mode": NOVA_SERVER_MODE,
            "deployment_mode": row["deployment_mode"] or DEPLOYMENT_MODE_DEDICATED,
        },
        "discovery": {
            "enabled": bool(row["discovery_enabled"]),
            "slug": row["discovery_slug"],
            "service_endpoint": row["service_endpoint"] or "",
        },
        # Sieges consommes : la seule vue qu'en donnait l'ancienne console.
        # Un compteur reel, pas une metrique inventee — c'est lui qui refuse
        # une connexion de plus quand le plafond est atteint.
        "seats": {"used": seats_in_use(organization_id), "limit": MAX_SEATS},
        # Un résumé par type : combien sont déclarés, combien servent. Ni
        # identifiant client, ni émetteur, ni secret — la vue détaillée existe
        # déjà, sous `provider_manage`.
        "providers": [
            {
                "provider_type": provider_type,
                "count": sum(1 for c in configs if c["provider_type"] == provider_type),
                "enabled_count": sum(
                    1 for c in configs
                    if c["provider_type"] == provider_type and c["enabled"]
                ),
            }
            for provider_type in sorted({c["provider_type"] for c in configs})
        ],
    }


@app.get("/api/control/organization")
def control_read_own_organization(
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    """Raccourci vers l'organisation que ce principal administre."""
    return control_organization_view(principal.organization_id)


@app.get("/api/control/organizations/{organization_id}")
def control_read_organization(
    organization_id: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    """Lecture d'une organisation par son identifiant.

    La forme cible du Control Plane : l'organisation est **désignée** par le
    chemin et **autorisée** par le principal. Fournir l'identifiant d'une autre
    organisation ne donne rien — pas même la confirmation qu'elle existe.
    """
    require_organization_access(principal, organization_id)
    return control_organization_view(organization_id)


# ────────────────── Policies produit : administration ─────────────────────
#
# Lecture pour tout administrateur ; ecriture reservee a `policy_manage`. La
# console reflete cette frontiere, mais c'est ici qu'elle est tenue : une
# interface en lecture seule n'est une protection pour personne.


def policy_view(organization_id: str) -> dict:
    """Ce que Nova Admin affiche. Aucun secret n'y figure — une policy est une
    regle de configuration, pas une donnee sensible."""
    policy = organization_policy(organization_id)
    return {
        "schema_version": policy["schema_version"],
        "revision": policy["revision"],
        # Ce qui s'applique reellement, defauts compris : c'est ce que la
        # console affiche.
        "settings": policy["settings"],
        # Ce qu'un administrateur a explicitement decide. La console peut ainsi
        # distinguer « laisse par defaut » de « choisi ainsi », et le jour ou un
        # defaut produit change, les organisations qui n'avaient rien demande
        # suivent.
        "overrides": policy["overrides"],
        "defaults": dict(DEFAULT_ORGANIZATION_POLICY),
        "updated_at": policy["updated_at"],
        "updated_by_user_id": policy["updated_by_user_id"],
        # `false` tant que personne n'a rien change : la console peut alors dire
        # « valeurs par defaut » plutot que de laisser croire a un choix
        # deliberement fait par quelqu'un.
        "configured": policy["configured"],
    }


@app.get("/api/control/organization/policy")
def control_read_policy(
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    return policy_view(principal.organization_id)


class PolicyUpdateRequest(BaseModel):
    # La revision sur laquelle l'administrateur a travaille. Obligatoire : sans
    # elle, deux consoles ouvertes cote a cote s'ecraseraient en silence, et la
    # derniere a cliquer gagnerait sans que personne ne l'apprenne.
    expected_revision: int
    settings: dict


@app.put("/api/control/organization/policy")
def control_update_policy(
    body: PolicyUpdateRequest,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_POLICY_MANAGE)),
):
    """Remplace la policy de l'organisation administree.

    Un remplacement complet, pas un correctif : le serveur normalise le
    document contre son schema, et une cle absente reprend son defaut. Le
    document stocke est donc toujours complet et toujours relisible.
    """
    try:
        overrides = validate_policy_settings(body.settings)
    except ValueError as error:
        reason = str(error)
        # Le motif porte la cle fautive — `POLICY_KEY_UNKNOWN:ai_skils` — pour
        # que la console puisse designer le champ plutot que de dire « erreur ».
        raise auth_error(400, reason)

    organization_id = principal.organization_id
    now = time.time()
    with db() as conn:
        current = conn.execute(
            "SELECT revision, settings FROM organization_policies WHERE organization_id=?",
            (organization_id,),
        ).fetchone()
        current_revision = current["revision"] if current else 0
        if body.expected_revision != current_revision:
            # 409, jamais un ecrasement. L'administrateur qui perdrait ainsi le
            # travail de son collegue ne saurait meme pas qu'il a existe.
            raise auth_error(409, "POLICY_REVISION_CONFLICT")

        previous = read_policy_settings(current["settings"]) if current else {}
        before = effective_policy_settings(previous)
        after = effective_policy_settings(overrides)
        changed = sorted(key for key in POLICY_SETTINGS if before[key] != after[key])

        if not changed and current is not None:
            # Enregistrer sans rien changer ne doit produire ni revision, ni
            # ligne d'audit. Une revision qui avance sans qu'aucune valeur ne
            # bouge rendrait le compteur illisible, et un journal qui note des
            # non-evenements finit par ne plus etre lu.
            return policy_view(organization_id)

        revision = current_revision + 1
        conn.execute(
            "INSERT INTO organization_policies(organization_id, schema_version, "
            "revision, settings, updated_at, updated_by_user_id) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(organization_id) DO UPDATE SET schema_version=excluded.schema_version, "
            "revision=excluded.revision, settings=excluded.settings, "
            "updated_at=excluded.updated_at, updated_by_user_id=excluded.updated_by_user_id",
            (organization_id, POLICY_SCHEMA_VERSION, revision, json.dumps(overrides),
             now, principal.user_id),
        )
        # Meme transaction que la mutation : une policy changee sans trace
        # rendrait le journal menteur par omission.
        record_admin_action(
            principal,
            "organization_policy.update",
            target_type="organization",
            target_id=organization_id,
            # Les **noms** des cles modifiees, pas le document. Savoir que
            # `ai_skills_enabled` a bouge suffit ; recopier la configuration
            # entiere a chaque ecriture ferait grossir le journal sans rien
            # apprendre de plus.
            metadata={
                "old_revision": current_revision,
                "new_revision": revision,
                "changed_keys": changed,
            },
            conn=conn,
        )
    return policy_view(organization_id)


# ───────────────────── Private AI runtime ────────────────────────────────


class AIRuntimeConfigUpdateRequest(BaseModel):
    expected_revision: int
    profile: str
    reasoning_allowed: bool
    concurrency_mode: str = "auto"
    max_concurrent_text: int | None = None
    max_concurrent_speech: int | None = None
    max_queue: int | None = None


def validate_ai_runtime_update(body: AIRuntimeConfigUpdateRequest) -> dict:
    try:
        profile = ModelProfile(body.profile.strip().lower())
    except ValueError as error:
        raise auth_error(400, "AI_RUNTIME_PROFILE_INVALID") from error
    if profile == ModelProfile.CUSTOM and not ORGANIZATION_AI_ROUTER.custom_available:
        # `custom` n'est pas un preset Nova : ses valeurs viennent du
        # deploiement. Tant qu'il n'en a pas declare, l'accepter enregistrerait
        # un profil qui se comporterait comme `standard` en pretendant autre
        # chose — et la console afficherait un reglage qui ne regle rien.
        raise auth_error(409, "AI_RUNTIME_PROFILE_UNAVAILABLE")
    mode = body.concurrency_mode.strip().lower()
    if mode not in {"auto", "manual"}:
        raise auth_error(400, "AI_RUNTIME_CONCURRENCY_MODE_INVALID")
    if mode == "auto":
        text, speech, queue = 2, 1, 4
    else:
        text = body.max_concurrent_text
        speech = body.max_concurrent_speech
        queue = body.max_queue
        if not all(isinstance(value, int) for value in (text, speech, queue)):
            raise auth_error(400, "AI_RUNTIME_CONCURRENCY_REQUIRED")
        if not (1 <= text <= 64 and 1 <= speech <= 32 and 0 <= queue <= 256):
            raise auth_error(400, "AI_RUNTIME_CONCURRENCY_INVALID")
    hardware = detect_hardware()
    if (
        NOVA_SERVER_MODE == SERVER_MODE_DEDICATED
        and profile == ModelProfile.ADVANCED
        and hardware.vram_gb is not None
        and hardware.vram_gb < 16
    ):
        raise auth_error(409, "AI_RUNTIME_HARDWARE_INCOMPATIBLE")
    return {
        "profile": profile,
        "reasoning_allowed": body.reasoning_allowed,
        "max_concurrent_text": int(text),
        "max_concurrent_speech": int(speech),
        "max_queue": int(queue),
        "concurrency_mode": mode,
    }


def available_profiles() -> list[str]:
    profiles = [
        ModelProfile.EFFICIENT.value,
        ModelProfile.STANDARD.value,
        ModelProfile.ADVANCED.value,
    ]
    if ORGANIZATION_AI_ROUTER.custom_available:
        profiles.append(ModelProfile.CUSTOM.value)
    return profiles


def custom_profile_view() -> dict:
    """Les parametres Custom, en lecture seule.

    Ils viennent de la configuration du deploiement et n'y retournent pas :
    aucune route ne les ecrit. Les montrer sert au diagnostic — savoir sous
    quels reglages le parc tourne — pas a les changer depuis un navigateur.
    """
    options = ORGANIZATION_AI_ROUTER.custom_options
    if options is None:
        return {"available": False}
    return {
        "available": True,
        "max_output_tokens": options.max_output_tokens,
        "temperature": options.temperature,
        "reasoning": options.reasoning.value,
    }


async def ai_runtime_admin_view(organization_id: str) -> dict:
    config = read_ai_runtime_config(organization_id)
    hardware = detect_hardware()
    runtime = await ORGANIZATION_AI_ROUTER.health()
    return {
        "schema_version": config["schema_version"],
        "processing_location": "organization_private_infrastructure",
        "deployment_mode": NOVA_SERVER_MODE,
        "desired": {
            "revision": config["desired_revision"],
            "profile": config["desired_profile"],
            "reasoning_allowed": bool(config["reasoning_allowed"]),
            "max_concurrent_text": config["max_concurrent_text"],
            "max_concurrent_speech": config["max_concurrent_speech"],
            "max_queue": config["max_queue"],
            "cloud_fallback": "disabled",
        },
        # La chaine complete, en trois faits distincts. « Livre » n'est pas
        # « applique » : un Connector peut avoir recu une revision et avoir
        # echoue a la poser. Les fondre afficherait un succes que rien ne
        # confirme.
        "delivered": {
            "revision": config["delivered_revision"],
            "delivered_at": config["delivered_at"],
        },
        "reported": {
            "applied_revision": config["applied_revision"],
            "applied_at": config["applied_at"],
            "apply_status": config["apply_status"],
            "last_apply_error": config["last_error_code"],
            "runtime_status": runtime["status"],
            "text_engine": runtime["text_engine"],
            "speech_engine": runtime["speech_engine"],
            "capacity": runtime["capacity"],
            "last_health_check": runtime["checked_at"],
        },
        "hardware": {
            "cpu": hardware.cpu,
            "architecture": hardware.architecture,
            "ram_gb": hardware.ram_gb,
            "gpu_vendor": hardware.gpu_vendor,
            "gpu_model": hardware.gpu_model,
            "vram_gb": hardware.vram_gb,
            "driver_version": hardware.driver_version,
        },
        "recommended_profile": recommend_profile(hardware).value,
        # Ce que ce deploiement permet reellement de choisir. La console s'en
        # sert plutot que d'afficher les quatre valeurs de l'enumeration : un
        # choix propose puis refuse a l'enregistrement n'est pas un choix.
        "available_profiles": available_profiles(),
        "custom_profile": custom_profile_view(),
        "remote_apply": remote_apply_view(organization_id),
        "connectors": connectors_for(organization_id),
    }


def remote_apply_view(organization_id: str) -> dict:
    """L'application a distance est-elle reellement possible aujourd'hui ?

    Elle ne l'est pas parce qu'un Connector existe en base : il faut qu'il soit
    joignable et qu'il declare savoir appliquer une configuration de runtime.
    Repondre oui sans cela promettrait une reconfiguration qui n'arriverait
    jamais.
    """
    if NOVA_SERVER_MODE == SERVER_MODE_DEDICATED:
        # Le serveur se configure lui-meme : aucun Connector n'est requis.
        return {"supported": True, "mode": "local", "requires_private_connector": False}
    capable = [
        connector
        for connector in connectors_for(organization_id)
        if connector["status"] in {CONNECTOR_STATUS_ONLINE, CONNECTOR_STATUS_DEGRADED}
        and "ai_runtime_config_v1" in connector["capabilities"]
    ]
    return {
        "supported": bool(capable),
        "mode": "connector",
        "requires_private_connector": True,
        "capable_connectors": len(capable),
    }


@app.get("/api/control/organization/ai-runtime")
async def control_read_ai_runtime(
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DIAGNOSTICS_READ)
    ),
):
    return await ai_runtime_admin_view(principal.organization_id)


@app.put("/api/control/organization/ai-runtime")
async def control_update_ai_runtime(
    body: AIRuntimeConfigUpdateRequest,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE)
    ),
):
    requested = validate_ai_runtime_update(body)
    organization_id = principal.organization_id
    now = time.time()
    with db() as conn:
        current = conn.execute(
            "SELECT * FROM organization_ai_runtime_config WHERE organization_id=?",
            (organization_id,),
        ).fetchone()
        current_revision = current["desired_revision"] if current else 0
        if body.expected_revision != current_revision:
            raise auth_error(409, "AI_RUNTIME_REVISION_CONFLICT")
        new_revision = current_revision + 1
        applied_revision = current["applied_revision"] if current else 0
        apply_status = (
            APPLY_STATUS_APPLYING
            if NOVA_SERVER_MODE == SERVER_MODE_DEDICATED
            # En plan de controle, la revision attend qu'un Connector vienne la
            # chercher. Elle n'est ni livree ni appliquee tant que cela n'a pas
            # eu lieu.
            else APPLY_STATUS_PENDING
        )
        conn.execute(
            "INSERT INTO organization_ai_runtime_config("
            "organization_id,schema_version,desired_revision,desired_profile,"
            "reasoning_allowed,max_concurrent_text,max_concurrent_speech,max_queue,"
            "applied_revision,apply_status,updated_at,updated_by_user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(organization_id) DO UPDATE SET "
            "schema_version=excluded.schema_version,desired_revision=excluded.desired_revision,"
            "desired_profile=excluded.desired_profile,reasoning_allowed=excluded.reasoning_allowed,"
            "max_concurrent_text=excluded.max_concurrent_text,"
            "max_concurrent_speech=excluded.max_concurrent_speech,max_queue=excluded.max_queue,"
            "apply_status=excluded.apply_status,updated_at=excluded.updated_at,"
            "updated_by_user_id=excluded.updated_by_user_id",
            (
                organization_id,
                AI_RUNTIME_CONFIG_SCHEMA_VERSION,
                new_revision,
                requested["profile"].value,
                int(requested["reasoning_allowed"]),
                requested["max_concurrent_text"],
                requested["max_concurrent_speech"],
                requested["max_queue"],
                applied_revision,
                apply_status,
                now,
                principal.user_id,
            ),
        )
        record_admin_action(
            principal,
            "ai_runtime.desired_config.update",
            target_type="organization",
            target_id=organization_id,
            metadata={
                "old_revision": current_revision,
                "new_revision": new_revision,
                "apply_mode": "local" if NOVA_SERVER_MODE == SERVER_MODE_DEDICATED else "connector_required",
            },
            conn=conn,
        )

    if NOVA_SERVER_MODE == SERVER_MODE_DEDICATED:
        config = read_ai_runtime_config(organization_id)
        try:
            apply_ai_runtime_config_locally(config)
        except AIRuntimeError as error:
            # `applied_revision` n'avance pas : la console dira « enregistre,
            # pas encore applique » plutot que d'afficher un etat que le
            # runtime ne porte pas. Le code voyage tel quel, sans type de tache
            # — aucune inference n'est en jeu ici.
            raise auth_error(409, error.code) from error
        with db() as conn:
            conn.execute(
                "UPDATE organization_ai_runtime_config SET applied_revision=?,apply_status=? "
                "WHERE organization_id=?",
                (new_revision, APPLY_STATUS_APPLIED, organization_id),
            )
    return await ai_runtime_admin_view(organization_id)


# ───────────────────── Private Connector ─────────────────────────────────
#
# ## Pourquoi un Connector
#
# Le plan de controle vit chez Nova ; le runtime IA vit chez l'organisation, sur
# un reseau prive. Pour appliquer une configuration a distance, Nova pourrait
# appeler l'infrastructure privee — il faudrait alors qu'un etablissement ouvre
# un port entrant vers Internet et publie une adresse interne. Personne ne
# devrait avoir a faire cela pour changer un profil de modele.
#
# Le sens de la connexion est donc inverse : c'est le Connector, depuis
# l'organisation, qui va chercher ce qu'il doit appliquer. Aucun port entrant,
# aucune adresse privee connue de Nova.
#
# ## Ce qui passe, et ce qui ne passe pas
#
# Le canal transporte des donnees de controle : identifiants techniques,
# revisions, statuts, versions, sante, resume materiel, profil choisi. Jamais
# d'audio, de prompt, de transcription, de sortie de modele, ni le contenu
# complet d'un Style ou d'un AI Skill. La frontiere de la Phase 32 ne bouge pas
# parce qu'on ajoute un tuyau.
#
# ## Ce que le Connector n'est pas
#
# Pas un VPN, pas un shell distant, pas un transfert de fichiers. Le protocole
# n'expose que des operations typees : appliquer une configuration de runtime,
# rapporter une sante. Il n'existe aucune commande generique a executer.


CONNECTOR_PROTOCOL_VERSION = 1
CONNECTOR_SUPPORTED_PROTOCOLS = frozenset({1})

# Ce que le plan de controle sait pousser. Il ne pousse a un Connector que ce
# que celui-ci declare comprendre : deduire une capacite d'un numero de version
# marcherait jusqu'au jour ou une version intermediaire existe.
CONNECTOR_KNOWN_CAPABILITIES = frozenset(
    {
        "ai_runtime_config_v1",
        "health_reporting_v1",
        "diagnostics_v1",
        # Un Connector d'avant la Phase 34 ne declare pas celle-ci. Le plan de
        # controle ne lui demandera donc jamais une rotation qu'il ne sait pas
        # conduire : la console dira « re-enroler » plutot que d'emettre un
        # ordre dans le vide.
        "credential_rotation_v1",
        # Deux mecanismes, deux contrats. `self` sait conduire sa propre
        # bascule ; `orchestrator` sait qu'une mise a jour existe mais qu'elle
        # appartient au deploiement. Les confondre ferait envoyer un ordre
        # d'auto-mise a jour a un conteneur, qui ne peut pas remplacer sa
        # propre image.
        "connector_update_v1",
        "connector_self_update_v1",
        "connector_orchestrator_update_v1",
    }
)
CONNECTOR_UPDATE_CAPABILITY = "connector_update_v1"
CONNECTOR_SELF_UPDATE_CAPABILITY = "connector_self_update_v1"
CONNECTOR_ORCHESTRATOR_UPDATE_CAPABILITY = "connector_orchestrator_update_v1"


def update_mechanism(capabilities: set[str]) -> str:
    """Qui applique la bascule sur ce deploiement.

    `none` n'est pas une erreur : un Connector d'avant cette phase ne declare
    aucun des deux, et la console doit le dire plutot que de supposer.
    """
    if CONNECTOR_SELF_UPDATE_CAPABILITY in capabilities:
        return "self"
    if CONNECTOR_ORCHESTRATOR_UPDATE_CAPABILITY in capabilities:
        return "external"
    return "none"
CONNECTOR_ROTATION_CAPABILITY = "credential_rotation_v1"

# La fenetre pendant laquelle l'ancien et le nouveau credential sont acceptes.
# Bornee : deux credentials durables actifs indefiniment doubleraient la surface
# d'attaque au lieu de la faire tourner.
CONNECTOR_ROTATION_TTL_SECONDS = int(
    os.getenv("NOVA_CONNECTOR_ROTATION_TTL_SECONDS", "600")
)
# Un Connector ou un administrateur compromis ne doit pas pouvoir fabriquer des
# milliers de secrets.
CONNECTOR_ROTATION_COOLDOWN_SECONDS = int(
    os.getenv("NOVA_CONNECTOR_ROTATION_COOLDOWN_SECONDS", "60")
)

ROTATION_STABLE = "stable"
ROTATION_REQUESTED = "requested"
ROTATION_PENDING = "pending"
ROTATION_FAILED = "failed"

# Un seul endroit pour les rythmes. Les repeter dans le Connector et dans le
# serveur les ferait diverger au premier ajustement.
CONNECTOR_HEARTBEAT_SECONDS = int(os.getenv("NOVA_CONNECTOR_HEARTBEAT_SECONDS", "45"))
CONNECTOR_OFFLINE_AFTER_SECONDS = CONNECTOR_HEARTBEAT_SECONDS * 4
CONNECTOR_DEGRADED_AFTER_SECONDS = CONNECTOR_HEARTBEAT_SECONDS * 2
CONNECTOR_ENROLLMENT_TTL_SECONDS = int(
    os.getenv("NOVA_CONNECTOR_ENROLLMENT_TTL_SECONDS", "900")
)

# Bornes de charge utile. Un Connector compromis ne doit pas pouvoir faire
# grossir la base ni la memoire du serveur en decrivant sa sante.
CONNECTOR_MAX_CAPABILITIES = 16
CONNECTOR_MAX_DIAGNOSTIC_FIELDS = 24
CONNECTOR_MAX_BODY_BYTES = 64 * 1024

CONNECTOR_STATUS_ONLINE = "online"
CONNECTOR_STATUS_DEGRADED = "degraded"
CONNECTOR_STATUS_OFFLINE = "offline"
CONNECTOR_STATUS_REVOKED = "revoked"

APPLY_STATUS_PENDING = "pending"
APPLY_STATUS_DELIVERED = "delivered"
APPLY_STATUS_APPLYING = "applying"
APPLY_STATUS_RESTART_REQUIRED = "restart_required"
APPLY_STATUS_APPLIED = "applied"
APPLY_STATUS_FAILED = "failed"
CONNECTOR_APPLY_STATUSES = frozenset(
    {
        APPLY_STATUS_PENDING,
        APPLY_STATUS_DELIVERED,
        APPLY_STATUS_APPLYING,
        APPLY_STATUS_RESTART_REQUIRED,
        APPLY_STATUS_APPLIED,
        APPLY_STATUS_FAILED,
    }
)


class ConnectorPrincipal:
    """Qui synchronise, pour quelle organisation.

    Un troisieme type de principal, a cote de l'utilisateur et de
    l'administrateur. Il n'herite d'aucune permission humaine : un Connector ne
    lit pas de membres, ne change pas de policy, ne consulte aucun contenu. Il
    porte une identite de deploiement, et rien d'autre.

    `organization_id` vient **de la base**, retrouve par l'empreinte du
    credential presente. Aucun champ de la requete ne peut l'influencer.
    """

    def __init__(self, row, presented: str = "current"):
        self.connector_id = row["connector_id"]
        self.organization_id = row["organization_id"]
        self.deployment_id = row["deployment_id"]
        # `current` ou `pending`. La confirmation de rotation exige que le
        # Connector se presente avec le **nouveau** credential : c'est la
        # preuve qu'il l'a recu et su le relire, et non qu'il l'a seulement
        # demande.
        self.presented = presented
        self.row = row


# Compteurs de cadence, par Connector. La cle est l'identite, pas le tenant :
# un Connector bavard ne doit pas faire taire les autres Connectors de son
# organisation.
# ------------------------------------------------------------- limitation
#
# Un seul compteur, partage entre tous les workers quand `NOVA_REDIS_URL` est
# defini. Il n'existe plus de second limiteur en memoire de processus : deux
# autorites de limitation donneraient deux verites, et c'est celle qui protege
# le moins qui gagnerait.
_RATE_LIMITER: ratelimit.RateLimiter | None = None
_RATE_LIMITER_LOCK = threading.Lock()


def rate_limiter() -> ratelimit.RateLimiter:
    """Le limiteur du processus. Construit au premier usage, jamais a l'import."""
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        with _RATE_LIMITER_LOCK:
            if _RATE_LIMITER is None:
                _RATE_LIMITER = ratelimit.RateLimiter()
    return _RATE_LIMITER


def reset_rate_limiter() -> None:
    """Oublie le limiteur du processus. Destine aux tests et aux recettes."""
    global _RATE_LIMITER
    with _RATE_LIMITER_LOCK:
        _RATE_LIMITER = None


def enforce_rate_limit(
    scope: str, identity: str, quota: ratelimit.Quota, code: str = "RATE_LIMITED"
) -> None:
    """Consomme un jeton, ou refuse.

    Le corps de la reponse ne porte qu'un code : ni l'identite comptee, ni le
    quota restant, ni la duree exacte. Un attaquant qui apprend quand sa
    fenetre se rouvre apprend a la contourner.
    """
    try:
        rate_limiter().check(scope, identity, quota)
    except ratelimit.RateLimitExceeded:
        raise auth_error(429, code)
    except ratelimit.LimiterUnavailable:
        # Route fail-closed sans compteur partage : on refuse. Sans compteur,
        # rien ne distingue un utilisateur d'un attaquant qui essaie mille fois.
        raise auth_error(503, "RATE_LIMIT_BACKEND_UNAVAILABLE")


# Le cooldown de rotation d'un Connector est une cadence, pas un quota de
# trafic : un seul passage par fenetre. La valeur reste celle de la Phase 34.
CONNECTOR_ROTATION_QUOTA = ratelimit.Quota(
    1,
    float(CONNECTOR_ROTATION_COOLDOWN_SECONDS),
    ratelimit.CLASS_CONNECTOR,
    ratelimit.FAIL_CLOSED,
)


def connector_rate_limit(scope: str, identity: str, quota: ratelimit.Quota) -> None:
    """Limite une route Connector avec le compteur **partage**.

    Le compteur en memoire de processus qui vivait ici a ete retire. Derriere
    quatre workers, il autorisait quatre fois la limite annoncee sans que rien
    ne le signale — et une limite qu'on croit avoir est pire qu'une limite
    qu'on sait ne pas avoir.
    """
    enforce_rate_limit(scope, identity, quota, "CONNECTOR_RATE_LIMITED")


def connector_principal(
    authorization: str | None = Header(default=None),
    content_length: int | None = Header(default=None),
) -> ConnectorPrincipal:
    if content_length is not None and content_length > CONNECTOR_MAX_BODY_BYTES:
        raise auth_error(413, "CONNECTOR_PAYLOAD_TOO_LARGE")
    scheme, _, credential = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise auth_error(401, "CONNECTOR_CREDENTIAL_REQUIRED")
    presented_hash = hash_token(credential.strip())
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connectors WHERE credential_hash=?", (presented_hash,)
        ).fetchone()
        presented = "current"
        if row is None:
            # Pendant une rotation, le nouveau credential vaut deja. Sans cette
            # fenetre, un Connector qui a recu son nouveau secret mais pas
            # encore confirme se retrouverait enferme dehors.
            row = conn.execute(
                "SELECT * FROM connectors WHERE pending_credential_hash=?",
                (presented_hash,),
            ).fetchone()
            presented = "pending"
        if row is not None and row["pending_credential_hash"] and expired_rotation(row):
            row = clear_expired_rotation(conn, row)
            if presented == "pending":
                # Le secret presente vient d'expirer : il n'authentifie plus.
                row = None
    # Revoque et inconnu donnent la meme reponse : un credential vole ne doit
    # pas permettre de distinguer « ce Connector a existe » de « il n'a jamais
    # existe ».
    if row is None or row["revoked_at"] is not None:
        raise auth_error(401, "CONNECTOR_CREDENTIAL_INVALID")
    connector_rate_limit("sync", row["connector_id"], ratelimit.CONNECTOR_SYNC)
    return ConnectorPrincipal(row, presented)


def expired_rotation(row) -> bool:
    started = row["pending_created_at"] or 0
    return time.time() - started > CONNECTOR_ROTATION_TTL_SECONDS


def clear_expired_rotation(conn, row):
    """Abandonne une rotation qui n'a jamais ete confirmee.

    Le credential courant reste valide. Echouer dans l'autre sens — invalider
    l'ancien parce que le nouveau n'est pas arrive — enfermerait dehors un
    Connector qui n'a rien fait de mal, et transformerait un incident reseau en
    intervention manuelle.
    """
    conn.execute(
        "UPDATE connectors SET pending_credential_hash=NULL, pending_created_at=NULL, "
        "rotation_state=? WHERE connector_id=?",
        (ROTATION_FAILED, row["connector_id"]),
    )
    record_connector_action(
        row["organization_id"],
        row["connector_id"],
        "connector.rotation.failed",
        {"reason": "CONNECTOR_ROTATION_EXPIRED"},
        conn=conn,
    )
    return conn.execute(
        "SELECT * FROM connectors WHERE connector_id=?", (row["connector_id"],)
    ).fetchone()


def record_connector_action(
    organization_id: str,
    connector_id: str,
    action: str,
    metadata: dict | None = None,
    conn=None,
):
    """Trace d'audit dont l'acteur est une machine.

    `record_admin_action` exige un principal humain ; un Connector n'en est pas
    un. Plutot que d'en fabriquer un faux — ce qui attribuerait a une personne
    des actions qu'elle n'a pas faites — l'acteur est nomme pour ce qu'il est.

    Les metadonnees suivent la meme regle qu'ailleurs : ni credential, ni code
    d'enrolement, ni contenu utilisateur.
    """

    def write(connection):
        connection.execute(
            "INSERT INTO admin_audit_log(id, organization_id, actor_user_id, "
            "actor_role, action, target_type, target_id, request_id, metadata, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                organization_id,
                None,
                "connector",
                action,
                "connector",
                connector_id,
                None,
                json.dumps(metadata or {}, ensure_ascii=False),
                time.time(),
            ),
        )

    if conn is not None:
        write(conn)
    else:
        with db() as connection:
            write(connection)


def connector_status(row) -> str:
    """L'etat vu du plan de controle, deduit du dernier contact.

    Un Connector revoque le reste : il ne redevient pas « hors ligne » parce
    qu'il a cesse d'appeler. La distinction compte pour qui lit la console.
    """
    if row["revoked_at"] is not None:
        return CONNECTOR_STATUS_REVOKED
    last_seen = row["last_seen_at"]
    if last_seen is None:
        return CONNECTOR_STATUS_OFFLINE
    age = time.time() - last_seen
    if age > CONNECTOR_OFFLINE_AFTER_SECONDS:
        return CONNECTOR_STATUS_OFFLINE
    if age > CONNECTOR_DEGRADED_AFTER_SECONDS:
        return CONNECTOR_STATUS_DEGRADED
    return CONNECTOR_STATUS_ONLINE


def connector_view(row) -> dict:
    """Ce que la console voit d'un Connector. Aucun credential, meme hache."""
    try:
        capabilities = json.loads(row["capabilities"] or "[]")
    except (ValueError, TypeError):
        capabilities = []
    try:
        runtime_state = json.loads(row["runtime_state"] or "null")
    except (ValueError, TypeError):
        runtime_state = None
    return {
        "connector_id": row["connector_id"],
        "deployment_id": row["deployment_id"],
        "display_name": row["display_name"],
        "status": connector_status(row),
        "protocol_version": row["protocol_version"],
        "protocol_supported": row["protocol_version"] in CONNECTOR_SUPPORTED_PROTOCOLS,
        "connector_version": row["connector_version"],
        "server_version": row["server_version"],
        "capabilities": capabilities,
        "runtime_state": runtime_state,
        "delivered_revision": row["delivered_revision"],
        "applied_revision": row["applied_revision"],
        "apply_status": row["apply_status"],
        "last_error_code": row["last_error_code"],
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
        "revoked_at": row["revoked_at"],
        # L'age du credential et l'etat de la bascule. Ni empreinte, ni
        # fragment de secret : rien de tout cela n'aiderait a exploiter la
        # console, et tout cela aiderait a exploiter une fuite.
        "platform": {
            "os": row["platform_os"],
            "arch": row["platform_arch"],
            "distribution": row["distribution_mode"],
            # Un conteneur ne remplace pas sa propre image. Le dire ici evite
            # d'offrir un bouton qui echouerait a chaque fois.
            "self_update_supported": CONNECTOR_SELF_UPDATE_CAPABILITY in capabilities,
            "update_mechanism": update_mechanism(set(capabilities)),
        },
        "update": {
            "state": row["update_state"],
            "policy": row["update_policy"],
            "target_version": row["target_version"],
            "last_known_good_version": row["last_known_good_version"],
            "last_update_at": row["last_update_at"],
            "last_error_code": row["last_update_error"],
            "support_status": connector_support_status(row["connector_version"]),
            "recommended_version": recommended_connector_version(),
            "minimum_supported_version": minimum_supported_connector_version(),
        },
        "credential": {
            "generation": row["credential_generation"],
            "created_at": row["credential_created_at"] or row["created_at"],
            "age_days": round(
                (time.time() - (row["credential_created_at"] or row["created_at"]))
                / 86400,
                1,
            ),
            "rotation_state": row["rotation_state"],
            "rotation_requested_at": row["rotation_requested_at"],
            "last_rotated_at": row["last_rotated_at"],
            "rotation_supported": CONNECTOR_ROTATION_CAPABILITY in capabilities,
        },
    }


def connectors_for(organization_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM connectors WHERE organization_id=? ORDER BY created_at",
            (organization_id,),
        ).fetchall()
    return [connector_view(row) for row in rows]


def desired_ai_runtime_for_connector(organization_id: str) -> dict | None:
    """La configuration souhaitee, reduite a ce qu'un Connector doit appliquer.

    Ni URL de runtime, ni nom de modele, ni secret : ces valeurs appartiennent
    au deploiement et n'ont jamais transite par le plan de controle. Le
    Connector applique un comportement, pas une infrastructure.
    """
    config = read_ai_runtime_config(organization_id)
    if not config["desired_revision"]:
        return None
    return {
        "revision": config["desired_revision"],
        "profile": config["desired_profile"],
        "reasoning_allowed": bool(config["reasoning_allowed"]),
        "max_concurrent_text": config["max_concurrent_text"],
        "max_concurrent_speech": config["max_concurrent_speech"],
        "max_queue": config["max_queue"],
    }


# ── APIs d'administration ────────────────────────────────────────────────


class ConnectorEnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    deployment_id: str = Field(min_length=1, max_length=120)


@app.post("/api/control/connectors/enrollments")
def control_create_connector_enrollment(
    body: ConnectorEnrollmentRequest,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE)
    ),
):
    """Cree un code d'enrolement, affiche une seule fois.

    Le code n'est pas conserve : seule son empreinte l'est. Nova ne peut donc
    pas le reafficher, et une copie de la base ne permet d'enroler personne.
    """
    now = time.time()
    token = secrets.token_urlsafe(32)
    enrollment_id = uuid.uuid4().hex
    with db() as conn:
        conn.execute(
            "INSERT INTO connector_enrollments(enrollment_id, organization_id, "
            "deployment_id, display_name, token_hash, created_at, expires_at, "
            "created_by_user_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                enrollment_id,
                principal.organization_id,
                body.deployment_id.strip(),
                body.display_name.strip(),
                hash_token(token),
                now,
                now + CONNECTOR_ENROLLMENT_TTL_SECONDS,
                principal.user_id,
            ),
        )
        record_admin_action(
            principal,
            "connector.enrollment.create",
            target_type="connector_enrollment",
            target_id=enrollment_id,
            metadata={"deployment_id": body.deployment_id.strip()},
            conn=conn,
        )
    return {
        "enrollment_id": enrollment_id,
        # La seule fois ou ce code existe en clair hors du Connector.
        "enrollment_token": token,
        "expires_at": now + CONNECTOR_ENROLLMENT_TTL_SECONDS,
        "expires_in": CONNECTOR_ENROLLMENT_TTL_SECONDS,
        "deployment_id": body.deployment_id.strip(),
        "display_name": body.display_name.strip(),
    }


@app.get("/api/control/connectors")
def control_list_connectors(
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DIAGNOSTICS_READ)
    ),
):
    with db() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM connector_enrollments WHERE organization_id=? "
            "AND consumed_at IS NULL AND expires_at > ?",
            (principal.organization_id, time.time()),
        ).fetchone()[0]
    return {
        "connectors": connectors_for(principal.organization_id),
        "pending_enrollments": pending,
        "protocol_version": CONNECTOR_PROTOCOL_VERSION,
        "heartbeat_seconds": CONNECTOR_HEARTBEAT_SECONDS,
        "offline_after_seconds": CONNECTOR_OFFLINE_AFTER_SECONDS,
    }


@app.post("/api/control/connectors/{connector_id}/revoke")
def control_revoke_connector(
    connector_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE)
    ),
):
    """Revoque un Connector. Le plan de donnees local, lui, continue.

    Revoquer coupe la synchronisation, pas la dictee : une organisation qui
    retire un Connector ne doit pas perdre du meme geste la fonction que ses
    membres utilisent.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=? AND organization_id=?",
            (connector_id, principal.organization_id),
        ).fetchone()
        if row is None:
            raise auth_error(404, "CONNECTOR_NOT_FOUND")
        if row["revoked_at"] is None:
            conn.execute(
                "UPDATE connectors SET revoked_at=? WHERE connector_id=?",
                (time.time(), connector_id),
            )
            record_admin_action(
                principal,
                "connector.revoke",
                target_type="connector",
                target_id=connector_id,
                conn=conn,
            )
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=?", (connector_id,)
        ).fetchone()
    return connector_view(row)


@app.post("/api/control/connectors/{connector_id}/rotate")
def control_request_connector_rotation(
    connector_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE)
    ),
):
    """Demande une rotation. L'administrateur ne recoit aucun secret.

    C'est le point du dessin : la console pose un drapeau, le Connector fait la
    rotation lui-meme lors de sa prochaine synchronisation. Faire transiter un
    credential durable par un navigateur d'administration en ferait la piece la
    plus exposee de la chaine.

    Une demande n'est pas une revocation. Un credential compromis se revoque —
    la rotation, elle, suppose que l'ancien fonctionne encore assez longtemps
    pour poser le nouveau.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=? AND organization_id=?",
            (connector_id, principal.organization_id),
        ).fetchone()
        if row is None:
            raise auth_error(404, "CONNECTOR_NOT_FOUND")
        if row["revoked_at"] is not None:
            raise auth_error(409, "CONNECTOR_REVOKED")
        try:
            capabilities = set(json.loads(row["capabilities"] or "[]"))
        except (ValueError, TypeError):
            capabilities = set()
        if CONNECTOR_ROTATION_CAPABILITY not in capabilities:
            # Emettre un ordre qu'un Connector ne sait pas lire le laisserait
            # « en rotation » pour toujours. La console dira de le mettre a jour
            # ou de le re-enroler.
            raise auth_error(409, "CONNECTOR_ROTATION_UNSUPPORTED")
        if row["rotation_state"] in {ROTATION_REQUESTED, ROTATION_PENDING}:
            raise auth_error(409, "CONNECTOR_ROTATION_IN_PROGRESS")
        conn.execute(
            "UPDATE connectors SET rotation_state=?, rotation_requested_at=? "
            "WHERE connector_id=?",
            (ROTATION_REQUESTED, time.time(), connector_id),
        )
        record_admin_action(
            principal,
            "connector.rotation.requested",
            target_type="connector",
            target_id=connector_id,
            conn=conn,
        )
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=?", (connector_id,)
        ).fetchone()
    return connector_view(row)


# ── APIs du Connector ────────────────────────────────────────────────────


class ConnectorEnrollBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrollment_token: str = Field(min_length=16, max_length=256)
    protocol_version: int
    connector_version: str = Field(max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=CONNECTOR_MAX_CAPABILITIES)


class ConnectorHealthBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_status: str = Field(default="unavailable", max_length=32)
    text_engine: str = Field(default="unavailable", max_length=32)
    speech_engine: str = Field(default="unavailable", max_length=32)
    queue_length: int = Field(default=0, ge=0, le=1_000_000)
    gpu_model: str | None = Field(default=None, max_length=120)
    vram_gb: float | None = Field(default=None, ge=0, le=10_000)
    last_error_code: str | None = Field(default=None, max_length=64)


class ConnectorPlatformBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    os: str = Field(default="", max_length=32)
    arch: str = Field(default="", max_length=32)
    distribution: str = Field(default="", max_length=32)


class ConnectorSyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int
    connector_version: str = Field(max_length=64)
    server_version: str = Field(default="", max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=CONNECTOR_MAX_CAPABILITIES)
    # Reception et application sont deux faits distincts : un Connector peut
    # avoir recu une revision sans avoir reussi a l'appliquer.
    applied_revision: int = Field(default=0, ge=0)
    apply_status: str = Field(default=APPLY_STATUS_PENDING, max_length=32)
    last_error_code: str | None = Field(default=None, max_length=64)
    health: ConnectorHealthBody | None = None
    # Ce sur quoi ce Connector tourne, tel qu'il le declare. Sert a ne lui
    # proposer qu'un artefact installable chez lui, jamais a lui accorder quoi
    # que ce soit : l'autorisation vient toujours du credential.
    platform: ConnectorPlatformBody | None = None
    update_state: str = Field(default="up_to_date", max_length=32)
    # Horodatage local, purement indicatif. L'autorite temporelle est le
    # serveur : l'horloge d'une machine distante n'engage personne.
    local_time: float | None = None


def validated_capabilities(declared: list[str]) -> list[str]:
    return sorted({item for item in declared if item in CONNECTOR_KNOWN_CAPABILITIES})


def require_supported_protocol(protocol_version: int) -> None:
    if protocol_version not in CONNECTOR_SUPPORTED_PROTOCOLS:
        raise auth_error(426, "CONNECTOR_PROTOCOL_UNSUPPORTED")


@app.post("/api/connector/enroll")
def connector_enroll(body: ConnectorEnrollBody):
    """Echange un code a usage unique contre une identite durable.

    L'organisation n'est jamais annoncee par le Connector : elle est portee par
    le code d'enrolement, qu'un administrateur authentifie a cree pour son
    propre tenant. C'est ce qui empeche un Connector de se declarer chez
    quelqu'un d'autre.
    """
    require_supported_protocol(body.protocol_version)
    token = body.enrollment_token.strip()
    connector_rate_limit("enroll", hash_token(token), ratelimit.CONNECTOR_ENROLL)
    now = time.time()
    credential = secrets.token_urlsafe(40)
    connector_id = uuid.uuid4().hex
    with db() as conn:
        enrollment = conn.execute(
            "SELECT * FROM connector_enrollments WHERE token_hash=?",
            (hash_token(token),),
        ).fetchone()
        # Inconnu, deja consomme, expire : une seule reponse. Distinguer les
        # trois cas dirait a qui sonde lequel de ses essais a « presque » marche.
        if (
            enrollment is None
            or enrollment["consumed_at"] is not None
            or enrollment["expires_at"] <= now
        ):
            raise auth_error(401, "CONNECTOR_ENROLLMENT_INVALID")
        conn.execute(
            "INSERT INTO connectors(connector_id, organization_id, deployment_id, "
            "display_name, credential_hash, protocol_version, connector_version, "
            "capabilities, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                connector_id,
                enrollment["organization_id"],
                enrollment["deployment_id"],
                enrollment["display_name"],
                hash_token(credential),
                body.protocol_version,
                body.connector_version,
                json.dumps(validated_capabilities(body.capabilities)),
                now,
            ),
        )
        conn.execute(
            "UPDATE connector_enrollments SET consumed_at=?, consumed_by_connector_id=? "
            "WHERE enrollment_id=?",
            (now, connector_id, enrollment["enrollment_id"]),
        )
        record_connector_action(
            enrollment["organization_id"],
            connector_id,
            "connector.enrolled",
            {"deployment_id": enrollment["deployment_id"]},
            conn=conn,
        )
    return {
        "connector_id": connector_id,
        # Delivre une seule fois. Le serveur n'en garde que l'empreinte.
        "connector_credential": credential,
        "organization_id": enrollment["organization_id"],
        "deployment_id": enrollment["deployment_id"],
        "protocol_version": CONNECTOR_PROTOCOL_VERSION,
        "heartbeat_seconds": CONNECTOR_HEARTBEAT_SECONDS,
    }


@app.post("/api/connector/sync")
def connector_sync(
    body: ConnectorSyncBody,
    principal: ConnectorPrincipal = Depends(connector_principal),
):
    """Un aller-retour : le Connector rapporte, le serveur repond ce qu'il veut.

    Rapport et battement de coeur sont le meme appel. Les separer donnerait deux
    rythmes a accorder et deux occasions de diverger, pour aucune information
    supplementaire.
    """
    require_supported_protocol(body.protocol_version)
    if body.apply_status not in CONNECTOR_APPLY_STATUSES:
        raise auth_error(400, "CONNECTOR_APPLY_STATUS_INVALID")

    now = time.time()
    organization_id = principal.organization_id
    capabilities = validated_capabilities(body.capabilities)
    health = body.health.model_dump() if body.health else None
    reported_platform = body.platform.model_dump() if body.platform else {}
    update_state = (
        body.update_state
        if body.update_state in connector_release.UPDATE_STATES
        else connector_release.UPDATE_UP_TO_DATE
    )

    desired = None
    if "ai_runtime_config_v1" in capabilities:
        desired = desired_ai_runtime_for_connector(organization_id)
    rotation_requested = (
        CONNECTOR_ROTATION_CAPABILITY in capabilities
        and principal.row["rotation_state"] in {ROTATION_REQUESTED, ROTATION_PENDING}
    )

    with db() as conn:
        current = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=?",
            (principal.connector_id,),
        ).fetchone()

        # Une revision appliquee ne recule pas. Un Connector qui redemarre avec
        # un etat local perime ne doit pas faire regresser ce que le plan de
        # controle sait deja.
        applied = max(int(current["applied_revision"]), int(body.applied_revision))
        apply_status = body.apply_status
        delivered = int(current["delivered_revision"])
        if desired is not None:
            delivered = max(delivered, int(desired["revision"]))

        # Une intention satisfaite s'efface : garder `target_version` apres la
        # bascule ferait croire a une mise a jour perpetuellement en cours.
        target = current["target_version"]
        if target and body.connector_version == target:
            target = None
        conn.execute(
            "UPDATE connectors SET protocol_version=?, connector_version=?, "
            "server_version=?, capabilities=?, runtime_state=?, applied_revision=?, "
            "delivered_revision=?, apply_status=?, last_error_code=?, last_seen_at=?, "
            "platform_os=?, platform_arch=?, distribution_mode=?, update_state=?, "
            "target_version=? WHERE connector_id=?",
            (
                body.protocol_version,
                body.connector_version,
                body.server_version,
                json.dumps(capabilities),
                json.dumps(health) if health else None,
                applied,
                delivered,
                apply_status,
                body.last_error_code,
                now,
                reported_platform.get("os"),
                reported_platform.get("arch"),
                reported_platform.get("distribution"),
                update_state,
                target,
                principal.connector_id,
            ),
        )
        # « Livre » est un fait du plan de controle : c'est lui qui vient de
        # tendre la revision. Un Connector qui rapporte encore `pending` n'a pas
        # a effacer cette information — sinon la console montrerait une revision
        # souhaitee qui semble n'etre jamais partie.
        chain_status = apply_status
        if apply_status == APPLY_STATUS_PENDING and delivered > applied:
            chain_status = APPLY_STATUS_DELIVERED
        sync_connector_state_into_config(
            conn, organization_id, delivered, applied, chain_status,
            body.last_error_code, now,
        )
        if apply_status in {APPLY_STATUS_APPLIED, APPLY_STATUS_FAILED} and applied != int(
            current["applied_revision"]
        ):
            record_connector_action(
                organization_id,
                principal.connector_id,
                f"connector.apply.{apply_status}",
                {"revision": applied},
                conn=conn,
            )

    return {
        "protocol_version": CONNECTOR_PROTOCOL_VERSION,
        "connector_id": principal.connector_id,
        "heartbeat_seconds": CONNECTOR_HEARTBEAT_SECONDS,
        # Le serveur date la reponse : le Connector n'a pas a etre cru sur
        # l'heure qu'il est.
        "server_time": now,
        "desired": {"ai_runtime": desired} if desired else {},
        # Un drapeau, pas un ordre porteur de secret. Le Connector decide quand
        # il conduit la rotation, et c'est lui qui recevra le nouveau
        # credential — jamais la console.
        "rotation_requested": rotation_requested,
        # Une intention typee : une version, jamais une commande.
        "update": connector_update_payload(current)
        if CONNECTOR_UPDATE_CAPABILITY in capabilities
        else {},
        "update_mechanism": update_mechanism(set(capabilities)),
        # Les seules operations que ce protocole connait. Il n'y a pas de
        # commande generique, et il ne doit pas y en avoir.
        "supported_operations": ["apply_ai_runtime_config", "report_health"],
    }


def sync_connector_state_into_config(
    conn, organization_id: str, delivered: int, applied: int,
    apply_status: str, last_error_code: str | None, now: float,
) -> None:
    """Reporte l'etat du Connector dans la configuration de l'organisation.

    C'est ce qui rend la chaine visible dans Nova Admin : souhaite, livre,
    applique. `applied_revision` n'avance jamais toute seule — seul un Connector
    qui declare avoir applique la fait bouger.
    """
    row = conn.execute(
        "SELECT * FROM organization_ai_runtime_config WHERE organization_id=?",
        (organization_id,),
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "UPDATE organization_ai_runtime_config SET delivered_revision=?, "
        "delivered_at=?, applied_revision=?, applied_at=?, apply_status=?, "
        "last_error_code=? WHERE organization_id=?",
        (
            max(int(row["delivered_revision"] or 0), delivered),
            now if delivered else row["delivered_at"],
            max(int(row["applied_revision"] or 0), applied),
            now if apply_status == APPLY_STATUS_APPLIED else row["applied_at"],
            apply_status,
            last_error_code,
            organization_id,
        ),
    )


# ── Distribution du Connector ────────────────────────────────────────────
#
# Le plan de controle annonce une **intention** : « passe a telle version ».
# Il n'envoie jamais de commande, de chemin d'executable ni d'argument. Le
# Connector sait comment se mettre a jour ; un canal capable de le lui dicter
# serait un shell distant sous un autre nom.
#
# Il ne transporte pas non plus l'artefact : le binaire passe par la source de
# distribution, verifie par signature et par empreinte. Le faire transiter par
# la synchronisation melangerait le plan de controle et la chaine
# d'approvisionnement.

# Racine de confiance : la cle publique dont les releases doivent porter la
# signature. Sans elle, le plan de controle refuse de publier — accepter « en
# attendant » ouvrirait exactement ce que cette verification protege.
NOVA_CONNECTOR_RELEASE_PUBLIC_KEY = os.getenv(
    "NOVA_CONNECTOR_RELEASE_PUBLIC_KEY", ""
).strip()


def published_releases() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM connector_releases WHERE status=? ORDER BY released_at DESC",
            (connector_release.RELEASE_PUBLISHED,),
        ).fetchall()
    return [dict(row) for row in rows]


def recommended_connector_version() -> str | None:
    """La version publiee la plus recente, par ordre de version — pas de date.

    Publier une correction ancienne apres une version majeure ne doit pas faire
    reculer tout un parc.
    """
    best = None
    for row in published_releases():
        try:
            if best is None or connector_release.is_newer(
                row["connector_version"], best
            ):
                best = row["connector_version"]
        except connector_release.UpdateError:
            continue
    return best


def minimum_supported_connector_version() -> str:
    return os.getenv("NOVA_CONNECTOR_MINIMUM_VERSION", "0.0.0")


def connector_support_status(version: str | None) -> str:
    """Supporte, obsolete, ou trop ancien — sans jamais couper le plan local.

    Un Connector sous le minimum est signale, pas debranche : couper la
    synchronisation le priverait de la mise a jour qui le remettrait a niveau.
    """
    if not version:
        return "unknown"
    try:
        if connector_release.is_newer(minimum_supported_connector_version(), version):
            return "unsupported"
        recommended = recommended_connector_version()
        if recommended and connector_release.is_newer(recommended, version):
            return "update_available"
    except connector_release.UpdateError:
        return "unknown"
    return "up_to_date"


class ConnectorReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Le manifeste tel qu'il a ete signe, octet pour octet. Le re-serialiser
    # invaliderait la signature.
    manifest: str = Field(min_length=2, max_length=connector_release.MAX_MANIFEST_BYTES)
    signature: str = Field(min_length=16, max_length=512)


@app.post("/api/control/connector-releases")
def control_publish_connector_release(
    body: ConnectorReleaseBody,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE)
    ),
):
    """Enregistre une release, apres avoir verifie sa signature.

    Le plan de controle ne stocke jamais une release non signee : s'il le
    faisait, la verification cote Connector deviendrait la seule barriere, et
    un plan de controle compromis suffirait a distribuer n'importe quoi.
    """
    try:
        manifest = connector_release.verify_manifest(
            body.manifest.encode("utf-8"),
            bytes.fromhex(body.signature.strip()),
            NOVA_CONNECTOR_RELEASE_PUBLIC_KEY,
        )
    except ValueError as error:
        raise auth_error(400, connector_release.UPDATE_SIGNATURE_INVALID) from error
    except connector_release.UpdateError as error:
        raise auth_error(400, error.code) from error

    now = time.time()
    release_id = uuid.uuid4().hex
    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM connector_releases WHERE connector_version=?",
            (manifest.connector_version,),
        ).fetchone()
        if existing is not None:
            # Une release publiee est immuable. Corriger un artefact se fait en
            # publiant une nouvelle version, jamais en changeant le contenu
            # d'une version deja distribuee.
            raise auth_error(409, "CONNECTOR_RELEASE_IMMUTABLE")
        conn.execute(
            "INSERT INTO connector_releases(release_id, connector_version, "
            "protocol_version, status, manifest, signature, released_at, "
            "created_by_user_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                release_id,
                manifest.connector_version,
                manifest.protocol_version,
                manifest.status,
                body.manifest,
                body.signature.strip(),
                now,
                principal.user_id,
            ),
        )
        record_admin_action(
            principal,
            "connector.release.publish",
            target_type="connector_release",
            target_id=release_id,
            metadata={"version": manifest.connector_version, "status": manifest.status},
            conn=conn,
        )
    return connector_release_view(release_id)


def connector_release_view(release_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connector_releases WHERE release_id=?", (release_id,)
        ).fetchone()
    if row is None:
        raise auth_error(404, "CONNECTOR_RELEASE_NOT_FOUND")
    # Ni manifeste complet, ni signature : la console n'a pas a les porter, et
    # les afficher inviterait a les recopier ailleurs.
    return {
        "release_id": row["release_id"],
        "connector_version": row["connector_version"],
        "protocol_version": row["protocol_version"],
        "status": row["status"],
        "released_at": row["released_at"],
        "revoked_at": row["revoked_at"],
    }


@app.get("/api/control/connector-releases")
def control_list_connector_releases(
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DIAGNOSTICS_READ)
    ),
):
    with db() as conn:
        rows = conn.execute(
            "SELECT release_id FROM connector_releases ORDER BY released_at DESC"
        ).fetchall()
    return {
        "releases": [connector_release_view(row["release_id"]) for row in rows],
        "recommended_version": recommended_connector_version(),
        "minimum_supported_version": minimum_supported_connector_version(),
        "trust_root_configured": bool(NOVA_CONNECTOR_RELEASE_PUBLIC_KEY),
    }


@app.post("/api/control/connector-releases/{release_id}/revoke")
def control_revoke_connector_release(
    release_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE)
    ),
):
    """Retire une release de la distribution.

    Elle cesse d'etre installable. Les Connectors qui la portent deja ne sont
    pas coupes : les debrancher ferait d'un probleme de chaine
    d'approvisionnement une panne de service.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connector_releases WHERE release_id=?", (release_id,)
        ).fetchone()
        if row is None:
            raise auth_error(404, "CONNECTOR_RELEASE_NOT_FOUND")
        conn.execute(
            "UPDATE connector_releases SET status=?, revoked_at=? WHERE release_id=?",
            (connector_release.RELEASE_REVOKED, time.time(), release_id),
        )
        record_admin_action(
            principal,
            "connector.release.revoke",
            target_type="connector_release",
            target_id=release_id,
            metadata={"version": row["connector_version"]},
            conn=conn,
        )
    return connector_release_view(release_id)


class ConnectorUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Une version, et rien d'autre. Ni commande, ni chemin, ni argument.
    target_version: str = Field(min_length=5, max_length=32)


@app.post("/api/control/connectors/{connector_id}/update")
def control_request_connector_update(
    connector_id: str,
    body: ConnectorUpdateRequest,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE)
    ),
):
    """Demande une mise a jour. Intention typee, jamais une commande."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=? AND organization_id=?",
            (connector_id, principal.organization_id),
        ).fetchone()
        if row is None:
            raise auth_error(404, "CONNECTOR_NOT_FOUND")
        if row["revoked_at"] is not None:
            raise auth_error(409, "CONNECTOR_REVOKED")
        try:
            capabilities = set(json.loads(row["capabilities"] or "[]"))
        except (ValueError, TypeError):
            capabilities = set()
        if CONNECTOR_SELF_UPDATE_CAPABILITY not in capabilities:
            # P0 : ne jamais envoyer un ordre d'auto-mise a jour a un Connector
            # qui ne declare pas savoir la conduire. Un conteneur resterait
            # « en cours de mise a jour » indefiniment, et la console
            # afficherait une operation qui n'arrivera jamais.
            raise auth_error(409, "CONNECTOR_UPDATE_NOT_SUPPORTED_HERE")

        release = conn.execute(
            "SELECT * FROM connector_releases WHERE connector_version=?",
            (body.target_version.strip(),),
        ).fetchone()
        if release is None:
            raise auth_error(404, "CONNECTOR_RELEASE_NOT_FOUND")
        if release["status"] != connector_release.RELEASE_PUBLISHED:
            raise auth_error(409, connector_release.UPDATE_RELEASE_REVOKED)
        try:
            if not connector_release.is_newer(
                release["connector_version"], row["connector_version"] or "0.0.0"
            ):
                # Revenir en arriere passe par le retour arriere du Connector,
                # qui sait vers quelle version saine revenir. Le faire passer
                # par une demande arbitraire permettrait de rejouer une
                # ancienne release.
                raise auth_error(409, connector_release.UPDATE_DOWNGRADE_REFUSED)
        except connector_release.UpdateError as error:
            raise auth_error(400, error.code) from error

        conn.execute(
            "UPDATE connectors SET target_version=?, update_state=? WHERE connector_id=?",
            (release["connector_version"], connector_release.UPDATE_AVAILABLE, connector_id),
        )
        record_admin_action(
            principal,
            "connector.update.requested",
            target_type="connector",
            target_id=connector_id,
            metadata={
                "from_version": row["connector_version"],
                "to_version": release["connector_version"],
            },
            conn=conn,
        )
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=?", (connector_id,)
        ).fetchone()
    return connector_view(row)


def connector_update_payload(row) -> dict:
    """Ce que la synchronisation dit d'une mise a jour : une intention.

    Le manifeste n'est pas ici. Le Connector le recupere a la source de
    distribution et en verifie la signature lui-meme : le plan de controle
    n'est pas un maillon de la chaine d'approvisionnement.
    """
    return {
        "recommended_version": recommended_connector_version(),
        "minimum_supported_version": minimum_supported_connector_version(),
        "support_status": connector_support_status(row["connector_version"]),
        "requested_version": row["target_version"],
        "policy": row["update_policy"],
    }


class ConnectorRotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int
    connector_version: str = Field(max_length=64)


@app.post("/api/connector/credential/rotate")
def connector_rotate_credential(
    body: ConnectorRotateBody,
    principal: ConnectorPrincipal = Depends(connector_principal),
):
    """Emet un nouveau credential, sans invalider l'ancien.

    L'ordre est celui-ci et pas l'inverse : tant que le Connector n'a pas
    prouve qu'il sait relire le nouveau secret, l'ancien reste sa seule facon
    d'exister. Reveler le nouveau et revoquer l'ancien dans le meme geste
    transformerait la moindre ecriture disque ratee en re-enrolement manuel.

    Le Connector fait tourner **son** credential, jamais celui d'un autre :
    l'identite vient du principal authentifie, pas du corps.
    """
    require_supported_protocol(body.protocol_version)
    if principal.presented != "current":
        # Se presenter avec le credential en attente pour en demander un
        # troisieme ferait une chaine sans fin de secrets non confirmes.
        raise auth_error(409, "CONNECTOR_ROTATION_IN_PROGRESS")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=?",
            (principal.connector_id,),
        ).fetchone()
    # L'etat avant la cadence : « une bascule tourne deja » est une reponse plus
    # utile que « trop de requetes », et une boucle d'appels s'y heurte de toute
    # facon des le second essai. La cadence garde ce qu'elle doit garder — les
    # rotations qui aboutissent.
    if row["pending_credential_hash"]:
        raise auth_error(409, "CONNECTOR_ROTATION_IN_PROGRESS")
    connector_rate_limit(
        "rotate", principal.connector_id, CONNECTOR_ROTATION_QUOTA
    )
    now = time.time()
    credential = secrets.token_urlsafe(40)
    with db() as conn:
        conn.execute(
            "UPDATE connectors SET pending_credential_hash=?, pending_created_at=?, "
            "rotation_state=? WHERE connector_id=?",
            (hash_token(credential), now, ROTATION_PENDING, principal.connector_id),
        )
        record_connector_action(
            principal.organization_id,
            principal.connector_id,
            "connector.rotation.started",
            {"generation": row["credential_generation"] + 1},
            conn=conn,
        )
    return {
        # La seule fois ou ce secret existe en clair hors du Connector.
        "connector_credential": credential,
        "generation": row["credential_generation"] + 1,
        "expires_in": CONNECTOR_ROTATION_TTL_SECONDS,
        "confirm_required": True,
    }


@app.post("/api/connector/credential/confirm")
def connector_confirm_credential(
    body: ConnectorRotateBody,
    principal: ConnectorPrincipal = Depends(connector_principal),
):
    """Promeut le credential en attente. C'est ici que l'ancien meurt.

    L'appel doit etre authentifie avec le **nouveau** secret : le serveur ne
    croit pas le Connector sur parole, il constate qu'il sait s'en servir.

    Rejouable : un ACK repete sur un credential deja courant repond succes sans
    rien recreer ni invalider. La livraison etant au-moins-une-fois, un
    acquittement perdu ne doit pas couter une identite.
    """
    require_supported_protocol(body.protocol_version)
    now = time.time()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM connectors WHERE connector_id=?",
            (principal.connector_id,),
        ).fetchone()
        if principal.presented == "current" and not row["pending_credential_hash"]:
            # Rejeu : la rotation est deja finie, et le secret presente est
            # bien le courant. Rien a faire, et surtout rien a casser.
            return {
                "generation": row["credential_generation"],
                "rotation_state": row["rotation_state"],
                "already_confirmed": True,
            }
        if principal.presented != "pending":
            raise auth_error(409, "CONNECTOR_ROTATION_FAILED")
        conn.execute(
            "UPDATE connectors SET credential_hash=?, credential_created_at=?, "
            "credential_generation=?, pending_credential_hash=NULL, "
            "pending_created_at=NULL, rotation_state=?, rotation_requested_at=NULL, "
            "last_rotated_at=? WHERE connector_id=?",
            (
                row["pending_credential_hash"],
                now,
                row["credential_generation"] + 1,
                ROTATION_STABLE,
                now,
                principal.connector_id,
            ),
        )
        record_connector_action(
            principal.organization_id,
            principal.connector_id,
            "connector.rotation.completed",
            {"generation": row["credential_generation"] + 1},
            conn=conn,
        )
    return {
        "generation": row["credential_generation"] + 1,
        "rotation_state": ROTATION_STABLE,
        "already_confirmed": False,
    }


# ── Plan de donnees : appliquer localement ───────────────────────────────
#
# La logique d'application vit ici ; le canal par lequel on la demande vit dans
# `local_channel.py`. Les separer est le point de la Phase 34 : la surface
# interne n'est plus servie par le listener public, et il n'existe plus de
# secret statique partage pour l'atteindre.


async def apply_runtime_configuration(
    revision: int,
    profile_name: str,
    reasoning_allowed: bool,
    max_concurrent_text: int,
    max_concurrent_speech: int,
    max_queue: int,
) -> dict:
    """Valider, appliquer, verifier, puis seulement dire « applique ».

    Repondre avant la verification ferait remonter au plan de controle un
    succes que personne n'a observe. Une configuration refusee laisse l'ancienne
    active : un refus ne doit pas laisser le parc sans reglage du tout.
    """
    try:
        profile = ModelProfile(profile_name.strip().lower())
    except ValueError:
        return {"revision": revision, "status": APPLY_STATUS_FAILED,
                "error_code": "AI_RUNTIME_CONFIG_REJECTED"}
    if profile == ModelProfile.CUSTOM and not ORGANIZATION_AI_ROUTER.custom_available:
        return {"revision": revision, "status": APPLY_STATUS_FAILED,
                "error_code": "AI_RUNTIME_PROFILE_UNAVAILABLE"}
    hardware = detect_hardware()
    if (
        profile == ModelProfile.ADVANCED
        and hardware.vram_gb is not None
        and hardware.vram_gb < 16
    ):
        return {"revision": revision, "status": APPLY_STATUS_FAILED,
                "error_code": "AI_RUNTIME_HARDWARE_INCOMPATIBLE"}

    previous = {
        "profile": ORGANIZATION_AI_ROUTER.profile,
        "reasoning_allowed": ORGANIZATION_AI_ROUTER.reasoning_allowed,
        "max_concurrent_text": ORGANIZATION_AI_ROUTER.text_capacity.limit,
        "max_concurrent_speech": ORGANIZATION_AI_ROUTER.speech_capacity.limit,
        "max_queue": ORGANIZATION_AI_ROUTER.text_capacity.max_queue,
        "revision": ORGANIZATION_AI_ROUTER.applied_revision,
    }
    try:
        ORGANIZATION_AI_ROUTER.apply_local_config(
            profile=profile,
            reasoning_allowed=reasoning_allowed,
            max_concurrent_text=max_concurrent_text,
            max_concurrent_speech=max_concurrent_speech,
            max_queue=max_queue,
            revision=revision,
        )
    except AIRuntimeError as error:
        ORGANIZATION_AI_ROUTER.apply_local_config(**previous)
        return {"revision": revision, "status": APPLY_STATUS_FAILED,
                "error_code": error.code}

    health = await ORGANIZATION_AI_ROUTER.health()
    return {
        "revision": revision,
        "status": APPLY_STATUS_APPLIED,
        "runtime_status": health["status"],
        "text_engine": health["text_engine"]["status"],
        "speech_engine": health["speech_engine"]["status"],
    }


async def local_runtime_state() -> dict:
    """L'etat du runtime de CE serveur, pour le Connector local.

    Aucun contenu utilisateur, et aucune adresse : le Connector n'a pas besoin
    de savoir ou tourne le moteur pour rapporter qu'il va bien.
    """
    health = await ORGANIZATION_AI_ROUTER.health()
    return {
        "server_version": app.version,
        "applied_revision": ORGANIZATION_AI_ROUTER.applied_revision,
        "profile": ORGANIZATION_AI_ROUTER.profile.value,
        "runtime_status": health["status"],
        "text_engine": health["text_engine"]["status"],
        "speech_engine": health["speech_engine"]["status"],
        "queue_length": health["capacity"]["text"]["queue_length"],
    }


# ────────────────── Policies produit : cote membre ────────────────────────


# ─────────────── Packages : administration et distribution ────────────────
#
# Deux surfaces distinctes, et la separation est la propriete de securite :
#
#     /api/control/organizations/{org}/packages   administration
#     /api/organization/packages                  distribution aux membres
#
# La premiere voit les brouillons, les auteurs, les erreurs de validation. La
# seconde ne voit que ce qui est publie et actif. Melanger les deux exposerait
# un brouillon a un membre — c'est-a-dire du contenu que l'organisation n'a
# precisement pas decide de distribuer.


def package_row(conn, organization_id: str, package_id: str):
    """Une fiche de package, **cadree par l'organisation**.

    L'organisation fait partie de la requete, pas d'un controle qui suivrait :
    un identifiant appartenant a une autre organisation ne renvoie rien, quelle
    que soit la maniere dont il a ete obtenu.
    """
    return conn.execute(
        "SELECT * FROM organization_packages WHERE package_id=? AND organization_id=?",
        (package_id, organization_id),
    ).fetchone()


def package_version_row(conn, organization_id: str, version_id: str):
    return conn.execute(
        "SELECT * FROM organization_package_versions "
        "WHERE version_id=? AND organization_id=?",
        (version_id, organization_id),
    ).fetchone()


def package_view(conn, row) -> dict:
    """Ce que la console affiche pour une fiche.

    Metadonnees seulement — jamais le contenu. Une liste qui embarquerait
    chaque document deviendrait lourde a mesure que l'organisation publie, et
    personne ne lit trois cents entrees de vocabulaire dans un tableau.
    """
    versions = conn.execute(
        "SELECT version_id, version_number, status, validation_status, "
        "content_hash, created_at, published_at FROM organization_package_versions "
        "WHERE package_id=? ORDER BY version_number DESC",
        (row["package_id"],),
    ).fetchall()
    active = row["active_version_id"]
    return {
        "package_id": row["package_id"],
        "package_type": row["package_type"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "active_version_id": active,
        "active_version_number": next(
            (v["version_number"] for v in versions if v["version_id"] == active), None
        ),
        "revision": row["revision"],
        "updated_at": row["updated_at"],
        "versions": [
            {
                "version_id": v["version_id"],
                "version_number": v["version_number"],
                "status": v["status"],
                "validation_status": v["validation_status"],
                "content_hash": v["content_hash"],
                "created_at": v["created_at"],
                "published_at": v["published_at"],
                "active": v["version_id"] == active,
            }
            for v in versions
        ],
    }


class PackageCreateRequest(BaseModel):
    package_type: str
    name: str
    description: str = ""


class PackageDraftRequest(BaseModel):
    content: dict


class PackageDraftUpdateRequest(BaseModel):
    # La revision sur laquelle l'administrateur a travaille. Sans elle, deux
    # consoles ouvertes cote a cote s'ecraseraient en silence.
    expected_revision: int
    content: dict


def require_package_organization(principal: AdminPrincipal, organization_id: str):
    require_organization_access(principal, organization_id)


@app.get("/api/control/organizations/{organization_id}/packages")
def control_list_packages(
    organization_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    require_package_organization(principal, organization_id)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM organization_packages WHERE organization_id=? "
            "ORDER BY package_type, name",
            (organization_id,),
        ).fetchall()
        return {"packages": [package_view(conn, row) for row in rows]}


@app.post("/api/control/organizations/{organization_id}/packages")
def control_create_package(
    organization_id: str,
    body: PackageCreateRequest,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    require_package_organization(principal, organization_id)
    if body.package_type not in PACKAGE_TYPES:
        raise auth_error(400, "PACKAGE_TYPE_UNKNOWN")
    try:
        name = _text(body.name, "name", PACKAGE_NAME_MAX)
        description = _text(
            body.description, "description", PACKAGE_DESCRIPTION_MAX, required=False)
    except PackageValidationError as error:
        raise HTTPException(400, error.as_detail())

    package_id = uuid.uuid4().hex
    now = time.time()
    with db() as conn:
        conn.execute(
            "INSERT INTO organization_packages(package_id, organization_id, "
            "package_type, name, description, status, revision, created_at, "
            "created_by_user_id, updated_at, updated_by_user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (package_id, organization_id, body.package_type, name, description,
             PACKAGE_STATUS_DRAFT, 0, now, principal.user_id, now, principal.user_id),
        )
        record_admin_action(
            principal, "package.create", target_type="package", target_id=package_id,
            metadata={"package_type": body.package_type}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


@app.get("/api/control/organizations/{organization_id}/packages/{package_id}")
def control_read_package(
    organization_id: str,
    package_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    require_package_organization(principal, organization_id)
    with db() as conn:
        row = package_row(conn, organization_id, package_id)
        if row is None:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        return package_view(conn, row)


@app.post("/api/control/organizations/{organization_id}/packages/{package_id}/versions")
def control_create_draft(
    organization_id: str,
    package_id: str,
    body: PackageDraftRequest,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    """Ouvre un brouillon.

    Un seul brouillon a la fois : deux brouillons concurrents poseraient la
    question « lequel devient v4 ? », et toute reponse serait arbitraire.
    """
    require_package_organization(principal, organization_id)
    with db() as conn:
        package = package_row(conn, organization_id, package_id)
        if package is None:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        existing = conn.execute(
            "SELECT version_id FROM organization_package_versions "
            "WHERE package_id=? AND status=?", (package_id, VERSION_STATUS_DRAFT)
        ).fetchone()
        if existing is not None:
            raise auth_error(409, "PACKAGE_DRAFT_EXISTS")

        try:
            content, invalid = normalize_draft_content(
                package["package_type"], body.content)
        except PackageValidationError as error:
            raise HTTPException(400, error.as_detail())

        highest = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) n FROM "
            "organization_package_versions WHERE package_id=?", (package_id,)
        ).fetchone()["n"]
        version_id = uuid.uuid4().hex
        now = time.time()
        conn.execute(
            "INSERT INTO organization_package_versions(version_id, package_id, "
            "organization_id, version_number, schema_version, content, content_hash, "
            "status, validation_status, revision, created_at, created_by_user_id, "
            "updated_at, updated_by_user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (version_id, package_id, organization_id, highest + 1,
             PACKAGE_CONTENT_SCHEMA_VERSION, json.dumps(content, ensure_ascii=False),
             package_content_hash(content), VERSION_STATUS_DRAFT,
             "invalid" if invalid else "valid", 0,
             now, principal.user_id, now, principal.user_id),
        )
        record_admin_action(
            principal, "package.draft_create", target_type="package",
            target_id=package_id,
            metadata={"package_type": package["package_type"],
                      "version": highest + 1}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


@app.get("/api/control/organizations/{organization_id}/packages/{package_id}"
         "/versions/{version_id}")
def control_read_version(
    organization_id: str,
    package_id: str,
    version_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    """Le contenu d'une version, sur sa propre route.

    Separee de la liste a dessein : afficher un tableau de packages ne doit pas
    ramener chaque document au passage.
    """
    require_package_organization(principal, organization_id)
    with db() as conn:
        row = package_version_row(conn, organization_id, version_id)
        if row is None or row["package_id"] != package_id:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        return {
            "version_id": row["version_id"],
            "package_id": row["package_id"],
            "version_number": row["version_number"],
            "schema_version": row["schema_version"],
            "status": row["status"],
            "validation_status": row["validation_status"],
            "validation_error": row["validation_error"],
            "content_hash": row["content_hash"],
            "revision": row["revision"],
            "content": json.loads(row["content"]),
        }


@app.put("/api/control/organizations/{organization_id}/packages/{package_id}"
         "/versions/{version_id}")
def control_update_draft(
    organization_id: str,
    package_id: str,
    version_id: str,
    body: PackageDraftUpdateRequest,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    """Modifie un brouillon — **jamais** une version publiee.

    C'est la garantie qui rend le retour arriere fiable : ce qui a ete distribue
    reste exactement ce qui avait ete distribue. Une version publiee modifiable
    ferait de l'historique une reconstitution.
    """
    require_package_organization(principal, organization_id)
    with db() as conn:
        package = package_row(conn, organization_id, package_id)
        row = package_version_row(conn, organization_id, version_id)
        if package is None or row is None or row["package_id"] != package_id:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        if row["status"] != VERSION_STATUS_DRAFT:
            raise auth_error(409, "PACKAGE_VERSION_IMMUTABLE")
        if body.expected_revision != row["revision"]:
            raise auth_error(409, "PACKAGE_REVISION_CONFLICT")

        try:
            content, invalid = normalize_draft_content(
                package["package_type"], body.content)
        except PackageValidationError as error:
            raise HTTPException(400, error.as_detail())

        now = time.time()
        conn.execute(
            "UPDATE organization_package_versions SET content=?, content_hash=?, "
            "validation_status=?, validation_error=?, revision=revision+1, "
            "updated_at=?, updated_by_user_id=? WHERE version_id=?",
            (json.dumps(content, ensure_ascii=False), package_content_hash(content),
             "invalid" if invalid else "valid", invalid,
             now, principal.user_id, version_id),
        )
        record_admin_action(
            principal, "package.draft_update", target_type="package",
            target_id=package_id,
            metadata={"package_type": package["package_type"],
                      "version": row["version_number"]}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


@app.delete("/api/control/organizations/{organization_id}/packages/{package_id}"
            "/versions/{version_id}")
def control_delete_draft(
    organization_id: str,
    package_id: str,
    version_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    """Abandonne un brouillon. Une version publiee ne se supprime pas."""
    require_package_organization(principal, organization_id)
    with db() as conn:
        row = package_version_row(conn, organization_id, version_id)
        if row is None or row["package_id"] != package_id:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        if row["status"] != VERSION_STATUS_DRAFT:
            raise auth_error(409, "PACKAGE_VERSION_IMMUTABLE")
        conn.execute(
            "DELETE FROM organization_package_versions WHERE version_id=?", (version_id,))
        record_admin_action(
            principal, "package.draft_discard", target_type="package",
            target_id=package_id, metadata={"version": row["version_number"]}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


class PackagePublishRequest(BaseModel):
    # Publier sans activer permet de preparer une version et de la mettre en
    # service plus tard. Le defaut suit l'attente courante : on publie pour
    # distribuer.
    activate: bool = True


@app.post("/api/control/organizations/{organization_id}/packages/{package_id}"
          "/versions/{version_id}/publish")
def control_publish_version(
    organization_id: str,
    package_id: str,
    version_id: str,
    body: PackagePublishRequest,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    """Publie un brouillon, et l'active si demande.

    Tout se fait dans une transaction : la version passe en publie, l'active
    change, et l'audit s'ecrit. Une publication a moitie appliquee laisserait
    l'organisation dans un etat que personne n'a choisi.
    """
    require_package_organization(principal, organization_id)
    with db() as conn:
        package = package_row(conn, organization_id, package_id)
        row = package_version_row(conn, organization_id, version_id)
        if package is None or row is None or row["package_id"] != package_id:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        if row["status"] == VERSION_STATUS_PUBLISHED:
            raise auth_error(409, "PACKAGE_ALREADY_PUBLISHED")

        # Revalidation au moment de publier : le schema a pu evoluer depuis la
        # redaction du brouillon, et distribuer un contenu qui ne passerait plus
        # la validation serait publier une erreur connue.
        try:
            validate_package_content(package["package_type"],
                                     json.loads(row["content"]))
        except PackageValidationError as error:
            raise HTTPException(400, error.as_detail())

        now = time.time()
        conn.execute(
            "UPDATE organization_package_versions SET status=?, published_at=?, "
            "updated_at=?, updated_by_user_id=? WHERE version_id=?",
            (VERSION_STATUS_PUBLISHED, now, now, principal.user_id, version_id))
        previous = package["active_version_id"]
        if body.activate:
            conn.execute(
                "UPDATE organization_packages SET active_version_id=?, status=?, "
                "revision=revision+1, updated_at=?, updated_by_user_id=? "
                "WHERE package_id=?",
                (version_id, PACKAGE_STATUS_PUBLISHED, now, principal.user_id,
                 package_id))
        record_admin_action(
            principal, "package.version_publish", target_type="package",
            target_id=package_id,
            # Les numeros, jamais le contenu : un journal qui recopierait un
            # dictionnaire entier a chaque publication cesserait d'etre lisible,
            # et porterait du contenu qu'il n'a pas a porter.
            metadata={"package_type": package["package_type"],
                      "version": row["version_number"],
                      "activated": bool(body.activate)}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


@app.post("/api/control/organizations/{organization_id}/packages/{package_id}"
          "/versions/{version_id}/activate")
def control_activate_version(
    organization_id: str,
    package_id: str,
    version_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    """Change la version active — c'est aussi le retour arriere.

    Revenir a v1 ne reecrit pas v1 : l'active redesigne une version restee
    intacte. Il n'y a donc pas deux mecanismes, un pour avancer et un pour
    reculer, mais un seul pointeur.
    """
    require_package_organization(principal, organization_id)
    with db() as conn:
        package = package_row(conn, organization_id, package_id)
        row = package_version_row(conn, organization_id, version_id)
        if package is None or row is None or row["package_id"] != package_id:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        if row["status"] != VERSION_STATUS_PUBLISHED:
            raise auth_error(409, "PACKAGE_VERSION_NOT_PUBLISHED")

        previous = package["active_version_id"]
        if previous == version_id:
            return package_view(conn, package)
        previous_number = None
        if previous:
            earlier = package_version_row(conn, organization_id, previous)
            previous_number = earlier["version_number"] if earlier else None
        now = time.time()
        conn.execute(
            "UPDATE organization_packages SET active_version_id=?, status=?, "
            "revision=revision+1, updated_at=?, updated_by_user_id=? "
            "WHERE package_id=?",
            (version_id, PACKAGE_STATUS_PUBLISHED, now, principal.user_id, package_id))
        rollback = previous_number is not None and row["version_number"] < previous_number
        record_admin_action(
            principal,
            "package.version_rollback" if rollback else "package.version_activate",
            target_type="package", target_id=package_id,
            metadata={"package_type": package["package_type"],
                      "old_version": previous_number,
                      "new_version": row["version_number"]}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


@app.post("/api/control/organizations/{organization_id}/packages/{package_id}/archive")
def control_archive_package(
    organization_id: str,
    package_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    """Retire le package de la distribution, sans rien effacer.

    Archiver plutot que supprimer : les versions restent lisibles, et un retour
    en arriere ne demande pas de tout ressaisir. C'est le meme choix qu'en
    Phase 22 pour les fournisseurs d'identite.
    """
    require_package_organization(principal, organization_id)
    with db() as conn:
        package = package_row(conn, organization_id, package_id)
        if package is None:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        now = time.time()
        conn.execute(
            "UPDATE organization_packages SET status=?, active_version_id=NULL, "
            "revision=revision+1, updated_at=?, updated_by_user_id=? "
            "WHERE package_id=?",
            (PACKAGE_STATUS_ARCHIVED, now, principal.user_id, package_id))
        record_admin_action(
            principal, "package.archive", target_type="package", target_id=package_id,
            metadata={"package_type": package["package_type"]}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


@app.post("/api/control/organizations/{organization_id}/packages/{package_id}/restore")
def control_restore_package(
    organization_id: str,
    package_id: str,
    principal: AdminPrincipal = Depends(
        require_admin_capability(CAPABILITY_CONTENT_MANAGE)),
):
    """Sort un package de l'archive.

    Archiver n'efface rien — c'etait le principe. Encore fallait-il pouvoir
    revenir, sans quoi « archiver plutot que supprimer » etait une promesse que
    le produit ne tenait pas : un clic malencontreux devenait definitif.

    Le package repart en brouillon, pas en publie : sortir de l'archive ne doit
    pas redistribuer du contenu a l'improviste. C'est un geste separe —
    reactiver une version — et il merite de le rester.
    """
    require_package_organization(principal, organization_id)
    with db() as conn:
        package = package_row(conn, organization_id, package_id)
        if package is None:
            raise auth_error(404, "PACKAGE_NOT_FOUND")
        if package["status"] != PACKAGE_STATUS_ARCHIVED:
            raise auth_error(409, "PACKAGE_NOT_ARCHIVED")
        now = time.time()
        conn.execute(
            "UPDATE organization_packages SET status=?, revision=revision+1, "
            "updated_at=?, updated_by_user_id=? WHERE package_id=?",
            (PACKAGE_STATUS_DRAFT, now, principal.user_id, package_id))
        record_admin_action(
            principal, "package.restore", target_type="package",
            target_id=package_id,
            metadata={"package_type": package["package_type"]}, conn=conn)
        return package_view(conn, package_row(conn, organization_id, package_id))


# ─────────────────── Packages : distribution aux membres ──────────────────


# Quelle policy gouverne quel type. Un package actif dont la policy est fermee
# n'est **pas** distribue : le contenu existe, la permission non.
# Ce sont les noms **du serveur** — `styles`, pas `writingStyles` : le poste
# rebaptise certaines capacites, et confondre les deux vocabulaires ferait
# passer une faute de frappe pour un refus de policy.
POLICY_BY_PACKAGE_TYPE = {
    PACKAGE_TYPE_STYLE: "styles",
    PACKAGE_TYPE_AI_SKILL: "aiSkills",
    PACKAGE_TYPE_VOCABULARY: "dictionary",
}


def effective_packages(organization_id: str) -> dict:
    """Ce qu'un membre recoit : les versions actives, filtrees par les policies.

    Jamais un brouillon, jamais une version publiee mais inactive, jamais un
    package archive. Ce que l'organisation n'a pas decide de distribuer ne
    quitte pas la console.
    """
    capabilities = public_campus_config(organization_id)["capabilities"]
    with db() as conn:
        rows = conn.execute(
            "SELECT p.package_id, p.package_type, p.name, p.description, "
            "v.version_id, v.version_number, v.schema_version, v.content, "
            "v.content_hash, v.published_at "
            "FROM organization_packages p "
            "JOIN organization_package_versions v ON v.version_id=p.active_version_id "
            "WHERE p.organization_id=? AND p.status=? AND v.status=? "
            "ORDER BY p.package_type, p.name",
            (organization_id, PACKAGE_STATUS_PUBLISHED, VERSION_STATUS_PUBLISHED),
        ).fetchall()

    packages = []
    for row in rows:
        capability = POLICY_BY_PACKAGE_TYPE.get(row["package_type"])
        if capability is not None:
            if capability not in capabilities:
                # Une capacite mal nommee ferait taire la distribution en
                # silence, exactement comme un refus de policy. Mieux vaut une
                # panne bruyante qu'un contenu qui n'arrive jamais sans que
                # personne ne sache pourquoi.
                raise RuntimeError(
                    f"capacite inconnue pour le type {row['package_type']}: "
                    f"{capability}")
            if not capabilities[capability]:
                continue
        packages.append({
            # Prefixe : un Style d'organisation ne doit jamais pouvoir se
            # confondre avec un preset integre ni avec un Style personnel.
            "id": package_identifier(row["package_type"], row["package_id"]),
            "package_id": row["package_id"],
            "type": row["package_type"],
            "name": row["name"],
            "description": row["description"],
            "version": row["version_number"],
            "schema_version": row["schema_version"],
            "content_hash": row["content_hash"],
            "content": json.loads(row["content"]),
        })

    # Empreinte de l'ensemble distribue. Le poste sait ainsi qu'il detient deja
    # la bonne chose, sans comparer chaque document — et sans qu'aucune
    # infrastructure de cache n'ait a etre construite.
    fingerprint = hashlib.sha256(
        "|".join(f"{p['package_id']}:{p['version']}:{p['content_hash']}"
                 for p in packages).encode("utf-8")
    ).hexdigest()
    return {"packages": packages, "catalog_version": fingerprint}


@app.get("/api/organization/packages")
def member_read_packages(response: Response, account=Depends(current_account),
                         if_none_match: str = Header(default="")):
    """Le contenu distribue au membre connecte.

    L'organisation vient du **compte**, jamais d'un parametre : accepter un
    identifiant du client ferait de cette route un moyen de lire le contenu
    d'une autre organisation en changeant une chaine dans l'URL.

    `ETag` et `304` evitent de retelecharger un catalogue inchange. C'est tout
    ce qu'il faut ici : un poste demande rarement, et le contenu est petit.
    """
    organization_id = (
        account["organization_id"] if account is not None else None
    ) or nova_organization_id()
    catalog = effective_packages(organization_id)
    etag = f'W/"{catalog["catalog_version"]}"'
    response.headers["ETag"] = etag
    if if_none_match.strip() == etag:
        return Response(status_code=304, headers={"ETag": etag})
    # La consigne d'un AI Skill ne descend pas : le poste envoie un `skill_id`
    # et le serveur retrouve la consigne dans le package actif. La renvoyer ici
    # rendrait ce controle decoratif.
    #
    # Un Style, lui, garde la sienne. Ce n'est pas une inconsequence : la
    # consigne d'un Style *est* le Style, le membre y a droit par son
    # appartenance, et le moteur local du poste doit pouvoir l'appliquer sans
    # serveur. La lui retirer ne protegerait rien et supprimerait les Styles
    # d'organisation du catalogue.
    public_packages = []
    for package in catalog["packages"]:
        visible = dict(package)
        content = dict(package["content"])
        if package["type"] == PACKAGE_TYPE_AI_SKILL:
            content.pop("instruction", None)
        visible["content"] = content
        public_packages.append(visible)
    return {"packages": public_packages, "catalog_version": catalog["catalog_version"]}


@app.get("/api/organization/policy")
def member_read_policy(account=Depends(current_account)):
    """La policy applicable au membre connecte.

    Session utilisateur ordinaire — aucune session d'administration n'est
    requise pour savoir ce que l'on a le droit d'utiliser.

    L'organisation vient du **compte**, jamais d'un parametre : accepter un
    `organization_id` du client ferait de cette route un moyen de lire la
    configuration d'une autre organisation en changeant une chaine dans l'URL.
    """
    organization_id = (
        account["organization_id"] if account is not None else None
    ) or nova_organization_id()
    policy = organization_policy(organization_id)
    return {
        "schema_version": policy["schema_version"],
        "revision": policy["revision"],
        "settings": policy["settings"],
        # Ce que le poste doit reellement appliquer, deja calcule : le poste
        # porte le meme resolveur pour son interface, mais il n'a pas a
        # reconstruire la base a partir de bribes.
        "effective_capabilities": public_campus_config(organization_id)["capabilities"],
    }



# ───────────────────── Administrateurs de l'organisation ──────────────────
#
# Nova Admin gere les administrateurs de **son** organisation. Le premier
# administrateur, lui, continue de naitre de la ligne de commande : un endpoint
# « faites de moi un administrateur » resterait une porte derobee, et c'est
# aussi le chemin de recuperation quand plus personne ne peut se connecter.
#
# Ces routes ne creent personne. Elles attribuent un role a un compte **qui
# existe deja** — inviter ou provisionner des utilisateurs releve du
# provisioning, pas de cette phase.

# Combien de comptes une recherche peut renvoyer. Assez pour choisir quelqu'un,
# trop peu pour aspirer l'annuaire de l'organisation.
MEMBER_SEARCH_LIMIT = 25

AUDIT_PAGE_DEFAULT = 50
AUDIT_PAGE_MAX = 100


def administrator_view(row) -> dict:
    """Ce qu'un administrateur a besoin de savoir sur un autre.

    L'adresse est affichee — sans elle, on ne saurait pas de qui il s'agit —
    mais elle n'identifie rien : toutes les mutations passent par `user_id`.
    Ni sujet externe, ni tenant, ni jeton : rien de tout cela n'aide a decider,
    et tout cela elargirait la surface.
    """
    role = security_role_for(row)
    return {
        "user_id": row["user_id"],
        "email": row["email"],
        "security_role": role,
        "capabilities": sorted(capabilities_for_role(role)),
        "status": account_status(row),
        "identity_provider": row["identity_provider"],
        "last_seen": row["last_seen"],
    }


@app.get("/api/control/organizations/{organization_id}/administrators")
def control_list_administrators(
    organization_id: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_ORGANIZATION_READ)),
):
    """Les administrateurs de cette organisation, et personne d'autre."""
    require_organization_access(principal, organization_id)
    with db() as conn:
        rows = administrators_of(conn, organization_id)
        managers = identity_managers_of(conn, organization_id)
    return {
        "administrators": [administrator_view(row) for row in rows],
        # L'interface s'en sert pour desactiver l'action plutot que de la
        # proposer avant de la refuser. Le serveur refuse de toute facon.
        "sole_identity_manager": managers[0] if len(managers) == 1 else None,
    }


class SecurityRoleRequest(BaseModel):
    """Un role, et rien d'autre.

    Le client n'envoie **jamais** de capacites : il nomme un role, le serveur
    en deduit ce qu'il autorise. Laisser passer une liste de capacites
    reviendrait a laisser le navigateur ecrire la matrice d'autorisation.
    """

    security_role: str


@app.post("/api/control/organizations/{organization_id}/administrators/{user_id}/role")
def control_set_administrator_role(
    organization_id: str,
    user_id: str,
    body: SecurityRoleRequest,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Attribue, change ou retire un role d'administration.

    `identity_manage` est exige : un `it_admin` ne peut donc pas s'elever
    lui-meme, puisqu'il ne l'a pas. C'est la matrice qui l'interdit, pas un
    controle special — et c'est mieux ainsi, un controle special s'oublie.

    Retirer l'acces revient a remettre le compte au rang de membre. Ce n'est
    **pas** une suppression : la personne continue d'utiliser Nova, elle cesse
    seulement d'administrer.
    """
    require_organization_access(principal, organization_id)
    request_id = new_request_id()
    try:
        result = change_security_role(
            organization_id, user_id, body.security_role, principal.user_id
        )
    except ValueError:
        raise auth_error(400, "SECURITY_ROLE_INVALID")
    except LookupError:
        # Meme reponse qu'un compte inexistant : un identifiant venu d'une autre
        # organisation ne doit pas se distinguer d'un identifiant faux.
        raise auth_error(404, "USER_NOT_FOUND")
    except LastIdentityManager:
        raise auth_error(409, "ADMIN_LAST_IDENTITY_MANAGER")

    if result["changed"]:
        previous, current = result["previous_role"], result["security_role"]
        was_admin = previous in ADMIN_SECURITY_ROLES
        is_admin = current in ADMIN_SECURITY_ROLES
        action = (
            "admin_role.grant" if not was_admin and is_admin
            else "admin_role.revoke" if was_admin and not is_admin
            else "admin_role.change"
        )
        # Les roles ne sont pas des secrets : les inscrire rend le journal
        # lisible. L'adresse, elle, n'y est pas — `user_id` suffit a retrouver
        # la personne, et une adresse peut changer.
        record_admin_action(
            principal, action, target_type="user", target_id=user_id,
            metadata={"previous_role": previous, "security_role": current},
            request_id=request_id,
        )

    with db() as conn:
        updated = user_by_id(conn, user_id)
    return administrator_view(updated)


# ────────────────────────── Membres de l'organisation ─────────────────────
#
# Gestion des comptes : qui appartient a l'organisation, quel est son metier,
# son statut, ses appareils. C'est ce que l'ancienne console faisait, porte ici
# sous l'authentification moderne.
#
# Ce que ces routes ne montrent **jamais** : ce que les gens dictent. Ni audio,
# ni transcription, ni prompt, ni presse-papiers, ni « activite ». Administrer
# des acces n'est pas surveiller des personnes, et la frontiere doit tenir
# dans le code, pas seulement dans une intention.

MEMBER_PAGE_DEFAULT = 25
MEMBER_PAGE_MAX = 100

# Metiers acceptes, tels que la base les stocke aujourd'hui. Changer ce champ
# ne change **jamais** un privilege : `security_role` vit ailleurs, et c'est
# tout l'objet de la separation posee en Phase 13.
MEMBER_ROLES = ("student", "teacher", "staff", "partner")


def member_view(row, groups: bool = True) -> dict:
    """Vue d'administration d'un compte.

    Le sujet externe, le tenant et les jetons sont absents : rien de tout cela
    n'aide a decider d'un acces, et tout cela elargirait la surface.
    """
    security_role = security_role_for(row)
    view = {
        "user_id": row["user_id"],
        "email": row["email"],
        "member_type": member_type_for(row["role"]),
        "member_role": row["role"],
        "security_role": security_role,
        "status": account_status(row),
        "identity_provider": row["identity_provider"],
        "last_seen": row["last_seen"],
        "created_at": row["created_at"],
        "note": row["note"],
    }
    if groups:
        view["groups"] = groups_for(row)
    return view


def member_or_404(conn, organization_id: str, user_id: str):
    """Le compte, s'il appartient bien a cette organisation.

    Un identifiant venu d'ailleurs ne se distingue pas d'un identifiant faux :
    savoir qu'un compte existe dans une autre organisation est deja une
    information.
    """
    row = user_by_id(conn, user_id)
    if row is None or row["organization_id"] != organization_id:
        raise auth_error(404, "USER_NOT_FOUND")
    return row


def guard_last_identity_manager(conn, organization_id: str, user_id: str):
    """Refuse de retirer le dernier compte capable de nommer des administrateurs.

    Meme garde-fou que la Phase 26, applique ici a la **desactivation** : un
    compte suspendu ne peut plus administrer, donc suspendre le dernier
    gestionnaire ferme la porte aussi surement que lui retirer son role.

    **Le verrou n'est pas decoratif.** Lire puis verifier ne protege rien sous
    concurrence : deux transactions qui retirent chacune un des deux derniers
    gestionnaires lisent toutes deux « il en reste deux », concluent toutes
    deux que ce n'est pas le dernier, et laissent l'organisation sans personne
    pour nommer un administrateur. On verrouille donc les comptes
    d'administration de l'organisation avant de compter.
    """
    conn.lock_rows(
        "SELECT user_id FROM users WHERE organization_id=? AND security_role<>?",
        (organization_id, SECURITY_ROLE_MEMBER),
    )
    managers = identity_managers_of(conn, organization_id)
    if managers == [user_id]:
        raise auth_error(409, "ADMIN_LAST_IDENTITY_MANAGER")


@app.get("/api/control/organizations/{organization_id}/members")
def control_list_members(
    organization_id: str,
    query: str = "",
    status: str = "",
    limit: int = MEMBER_PAGE_DEFAULT,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Recherche et liste paginee des comptes de l'organisation.

    Une liste est permise sans terme de recherche — administrer suppose de voir
    qui est la — mais elle est **paginee et plafonnee**. Ce qui reste interdit,
    c'est l'export : pas de « tout renvoyer », pas de curseur illimite.
    """
    require_organization_access(principal, organization_id)
    limit = max(1, min(MEMBER_PAGE_MAX, limit))

    clauses = ["organization_id=?"]
    values: list = [organization_id]
    term = normalize_email(query)
    if term:
        clauses.append("normalized_email LIKE ?")
        values.append(f"%{term}%")
    if status in {"active", "disabled", "deprovisioned"}:
        # `disabled` reste la colonne booleenne que lit le reste du serveur ;
        # `status` la double. On interroge les deux pour rester coherent.
        if status == "active":
            clauses.append("(disabled IS NULL OR disabled=0)")
        elif status == "disabled":
            clauses.append("disabled=1")
        else:
            clauses.append("status=?")
            values.append(status)
    values.append(limit + 1)

    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM users WHERE {' AND '.join(clauses)} "
            f"ORDER BY normalized_email LIMIT ?",
            tuple(values),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE organization_id=?",
            (organization_id,),
        ).fetchone()["c"]

    has_more = len(rows) > limit
    return {
        "members": [member_view(row) for row in rows[:limit]],
        "has_more": has_more,
        "limit": limit,
        "total": total,
    }


@app.get("/api/control/organizations/{organization_id}/members/{user_id}")
def control_member_detail(
    organization_id: str,
    user_id: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Fiche d'un compte : identite, appartenance, appareils.

    Les appareils sont nommes par leur libelle et leur date — jamais par leur
    jeton, ni meme par son empreinte : une empreinte reste une valeur qui
    identifie une session, et elle n'aide personne a decider.
    """
    require_organization_access(principal, organization_id)
    with db() as conn:
        row = member_or_404(conn, organization_id, user_id)
        devices = conn.execute(
            "SELECT machine, active, created_at, expires_at FROM tokens "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        identities = conn.execute(
            "SELECT provider, verification, last_seen FROM federated_identities "
            "WHERE user_id=? AND organization_id=?",
            (user_id, organization_id),
        ).fetchall()
        admin_sessions = conn.execute(
            "SELECT COUNT(*) c FROM admin_sessions WHERE user_id=? AND revoked_at IS NULL",
            (user_id,),
        ).fetchone()["c"]

    return {
        "member": member_view(row),
        "devices": [
            {
                "machine": device["machine"],
                "active": bool(device["active"]),
                "created_at": device["created_at"],
                "expires_at": device["expires_at"],
            }
            for device in devices
        ],
        # Les methodes de connexion liees, sans `oid` ni `sub` : le detail
        # n'aide pas a administrer et n'a pas a circuler.
        "identities": [
            {
                "provider": identity["provider"],
                "verification": identity["verification"],
                "last_seen": identity["last_seen"],
            }
            for identity in identities
        ],
        "open_admin_sessions": admin_sessions,
    }


class MemberStatusRequest(BaseModel):
    status: str


@app.post("/api/control/organizations/{organization_id}/members/{user_id}/status")
def control_set_member_status(
    organization_id: str,
    user_id: str,
    body: MemberStatusRequest,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Suspend ou reactive un compte.

    Suspendre coupe tout de suite : sessions utilisateur et sessions
    d'administration deviennent inutilisables, et aucune nouvelle connexion
    n'est acceptee. Ce qui **survit** : l'identite federee, l'historique
    d'audit, le compte lui-meme. Suspendre n'est pas supprimer.

    Reactiver ne ressuscite aucune session : la personne se reconnecte. Rendre
    a quelqu'un ses anciens jetons serait la mauvaise facon de lui rendre son
    acces.
    """
    require_organization_access(principal, organization_id)
    wanted = (body.status or "").strip().lower()
    if wanted not in {"active", "disabled"}:
        raise auth_error(400, "MEMBER_STATUS_INVALID")

    with db() as conn:
        row = member_or_404(conn, organization_id, user_id)
        if wanted == "disabled":
            guard_last_identity_manager(conn, organization_id, user_id)
        previous = account_status(row)
        if previous == wanted:
            return {"member": member_view(row), "changed": False}

        conn.execute(
            "UPDATE users SET disabled=?, status=?, updated_at=? WHERE user_id=?",
            (1 if wanted == "disabled" else 0, wanted, time.time(), user_id),
        )
        if wanted == "disabled":
            conn.execute("UPDATE tokens SET active=0 WHERE user_id=?", (user_id,))
            conn.execute(
                "UPDATE admin_sessions SET revoked_at=? WHERE user_id=? "
                "AND revoked_at IS NULL",
                (time.time(), user_id),
            )
        # Meme transaction que la mutation : la trace ne peut pas manquer.
        record_admin_action(
            principal,
            "member.disable" if wanted == "disabled" else "member.enable",
            target_type="user", target_id=user_id,
            metadata={"previous_status": previous, "status": wanted},
            conn=conn,
        )
        updated = user_by_id(conn, user_id)

    print(f"[admin] member status target={user_id[:8]} to={wanted}")
    return {"member": member_view(updated), "changed": True}


class MemberProfileRequest(BaseModel):
    """Metier et groupe. **Aucun privilege ne passe par ici.**"""

    member_role: str | None = None
    cohort: str | None = None
    note: str | None = None


@app.post("/api/control/organizations/{organization_id}/members/{user_id}/profile")
def control_update_member_profile(
    organization_id: str,
    user_id: str,
    body: MemberProfileRequest,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Change le metier ou le groupe d'un membre.

    `security_role` n'est **pas** modifiable ici, et ce n'est pas un oubli : les
    roles d'administration se gerent depuis leur propre ecran. Deux endroits qui
    modifient le meme privilege finissent par diverger, et l'un des deux oublie
    un garde-fou.
    """
    require_organization_access(principal, organization_id)
    if body.member_role is not None and body.member_role not in MEMBER_ROLES:
        raise auth_error(400, "MEMBER_ROLE_INVALID")

    with db() as conn:
        row = member_or_404(conn, organization_id, user_id)
        updates: dict = {}
        if body.member_role is not None and body.member_role != row["role"]:
            updates["role"] = body.member_role
        if body.cohort is not None and body.cohort.strip() != (row["cohort"] or ""):
            updates["cohort"] = body.cohort.strip()
        if body.note is not None and body.note.strip() != (row["note"] or ""):
            updates["note"] = body.note.strip()
        if not updates:
            return {"member": member_view(row), "changed": False}

        assignments = ", ".join(f"{name}=?" for name in updates)
        conn.execute(
            f"UPDATE users SET {assignments}, updated_at=? WHERE user_id=?",
            (*updates.values(), time.time(), user_id),
        )
        record_admin_action(
            principal,
            "member.type_change" if "role" in updates else "member.group_change",
            target_type="user", target_id=user_id,
            metadata={"fields": sorted(updates)},
            conn=conn,
        )
        updated = user_by_id(conn, user_id)

    # Le privilege n'a pas bouge : le verifier plutot que l'affirmer.
    assert security_role_for(updated) == security_role_for(row)
    return {"member": member_view(updated), "changed": True}


@app.delete("/api/control/organizations/{organization_id}/members/{user_id}")
def control_delete_member(
    organization_id: str,
    user_id: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Efface un compte et ce qui s'y rattache.

    ## Pourquoi cette route existe malgre tout

    Suspendre vaut mieux que supprimer dans presque tous les cas : on garde
    l'historique, on peut revenir en arriere. Mais « presque » n'est pas
    « toujours » — une demande d'effacement est un droit, et sans chemin
    prevu elle se ferait en SQL, a la main, sans trace.

    ## Ce qui part, ce qui reste

    Partent : le compte, ses sessions, ses compteurs d'usage, ses identites
    federees. Reste : **le journal d'audit**, qui porte des actes
    d'administration et non des donnees personnelles de dictee — l'effacer
    reviendrait a effacer la trace de qui a fait quoi.

    Le dernier gestionnaire d'identites ne peut pas etre supprime, pour la
    meme raison qu'il ne peut pas etre suspendu.
    """
    require_organization_access(principal, organization_id)
    with db() as conn:
        row = member_or_404(conn, organization_id, user_id)
        guard_last_identity_manager(conn, organization_id, user_id)
        normalized = row["normalized_email"]
        for table in ("tokens", "usage", "personal_dictionary", "snippets",
                      "formatting_rules"):
            columns = set(conn.columns(table))
            if "user_id" in columns:
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        conn.execute(
            "DELETE FROM federated_identities WHERE user_id=? AND organization_id=?",
            (user_id, organization_id),
        )
        conn.execute(
            "DELETE FROM auth_codes WHERE lower(trim(email))=? AND organization_id=?",
            (normalized, organization_id),
        )
        conn.execute("DELETE FROM admin_sessions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        # L'audit conserve la trace de la suppression elle-meme.
        record_admin_action(
            principal, "member.delete", target_type="user", target_id=user_id,
            conn=conn,
        )
    print(f"[admin] member deleted target={user_id[:8]}")
    return {"deleted": user_id}


@app.post("/api/control/organizations/{organization_id}/members/{user_id}/sessions/revoke")
def control_revoke_member_sessions(
    organization_id: str,
    user_id: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Deconnecte une personne de tous ses appareils.

    Par `user_id` : l'homonyme d'une autre organisation garde ses sessions. Le
    compte reste actif et son identite federee intacte — la personne se
    reconnecte simplement.
    """
    require_organization_access(principal, organization_id)
    with db() as conn:
        member_or_404(conn, organization_id, user_id)
        devices = conn.execute(
            "UPDATE tokens SET active=0 WHERE user_id=? AND active=1", (user_id,)
        ).rowcount
        conn.execute(
            "UPDATE admin_sessions SET revoked_at=? WHERE user_id=? "
            "AND revoked_at IS NULL",
            (time.time(), user_id),
        )
        record_admin_action(
            principal, "member.sessions_revoke",
            target_type="user", target_id=user_id,
            metadata={"devices": devices}, conn=conn,
        )
    print(f"[admin] member sessions revoked target={user_id[:8]} devices={devices}")
    return {"revoked_devices": devices}


# ─────────────────────────── Journal d'audit ──────────────────────────────


AUDIT_ACTION_LABELS = {
    "scim_credential_created": "Created a SCIM credential",
    "scim_credential_revoked": "Revoked a SCIM credential",
    "scim_user_created": "Provisioned a SCIM user",
    "scim_user_updated": "Updated a SCIM user",
    "scim_user_suspended": "Suspended a SCIM user",
    "scim_user_reactivated": "Reactivated a SCIM user",
    "scim_user_deprovisioned": "Deprovisioned a SCIM user",
    "scim_group_created": "Created a SCIM group",
    "scim_group_updated": "Updated a SCIM group",
    "scim_group_deleted": "Deleted a SCIM group",
    "scim_group_membership_changed": "Changed SCIM group membership",
    "admin_session.create": "Opened an administration session",
    "admin_step_up.success": "Re-authenticated to administer",
    "admin_role.grant": "Granted administrator role",
    "admin_role.change": "Changed administrator role",
    "admin_role.revoke": "Removed administrator access",
    "provider_config.create": "Added an authentication provider",
    "provider_config.update": "Updated an authentication provider",
    "provider_config.disable": "Disabled an authentication provider",
    "discovery.update": "Updated organization discovery",
    "user.update": "Updated a member profile",
    "user.delete": "Deleted a member",
    "user.revoke_machines": "Signed a member out of every device",
    "cohort.disable": "Suspended a cohort",
}


@app.get("/api/control/organizations/{organization_id}/audit")
def control_read_audit(
    organization_id: str,
    limit: int = AUDIT_PAGE_DEFAULT,
    before: float | None = None,
    action: str | None = None,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_SECURITY_MANAGE)),
):
    """Activite d'administration recente, en lecture seule.

    `security_manage` plutot qu'une capacite dediee : lire qui a change quoi
    releve de la surveillance de la securite de l'organisation, et ajouter une
    capacite par ecran finirait par rendre la matrice illisible.

    **Pagination obligatoire.** Renvoyer la table entiere marcherait le premier
    mois puis deviendrait un telechargement, et un journal qu'on ne peut pas
    parcourir n'est pas consulte.

    Aucune route ne modifie ni ne supprime ce journal. Cela dit, l'ajout seul
    est garanti par l'application : quiconque tient le fichier de base tient le
    journal, et il ne faut pas pretendre le contraire.
    """
    require_organization_access(principal, organization_id)
    limit = max(1, min(AUDIT_PAGE_MAX, limit))
    clauses = ["organization_id=?"]
    values: list = [organization_id]
    if before is not None:
        clauses.append("created_at < ?")
        values.append(before)
    if action:
        clauses.append("action=?")
        values.append(action)
    values.append(limit + 1)

    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM admin_audit_log WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at DESC LIMIT ?",
            tuple(values),
        ).fetchall()
        # Les acteurs sont resolus en adresses pour que le journal se lise ;
        # un identifiant seul obligerait a une recherche pour chaque ligne.
        actors = {
            row["user_id"]: row["email"]
            for row in conn.execute(
                "SELECT user_id, email FROM users WHERE organization_id=?",
                (organization_id,),
            )
        }

    has_more = len(rows) > limit
    entries = []
    for row in rows[:limit]:
        entries.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "actor_user_id": row["actor_user_id"],
            # Absent quand l'acte vient du jeton herite, qui n'identifie personne.
            "actor": actors.get(row["actor_user_id"]),
            "actor_role": row["actor_role"],
            "action": row["action"],
            "action_label": AUDIT_ACTION_LABELS.get(row["action"], row["action"]),
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "target": actors.get(row["target_id"]),
            "request_id": row["request_id"],
            # Deja filtree a l'ecriture : ni secret, ni jeton, ni contenu dicte.
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
        })
    return {
        "entries": entries,
        # Curseur d'age : plus stable qu'un decalage numerique, que l'ecriture
        # d'une nouvelle ligne decalerait.
        "next_before": entries[-1]["created_at"] if has_more and entries else None,
        "limit": limit,
    }


# --- profil de l'utilisateur connecté (pour l'app)
@app.get("/api/me")
def me(user: str = Depends(current_user), u=Depends(current_account)):
    """Profil du membre connecté.

    **Strictement additif.** Les trois champs historiques `email`, `role` et
    `cohort` gardent leur nom, leur type et leur place à la racine ; le client
    actuel les désérialise sans voir le reste. Le champ `organization` reste
    une **chaîne** — le nom d'affichage — parce que le Desktop déployé le
    déclare déjà ainsi : en faire un objet casserait chaque poste installé.
    Les données structurées vivent donc dans des champs nouveaux, et la forme
    du contrat est annoncée par `contract_version`.
    """
    if u is None:
        return {"email": user}
    # L'organisation vient de la ligne du membre — jamais d'une entrée client,
    # et plus de l'organisation active : en multi-tenant, ce ne serait pas
    # forcément la sienne.
    organization_id = u["organization_id"] or nova_organization_id()
    with db() as conn:
        organization = conn.execute(
            "SELECT id, display_name, org_type FROM organizations WHERE id=?",
            (organization_id,),
        ).fetchone()

    # La policy du membre, pas celle de l'organisation active : en multi-tenant
    # ce ne serait pas la meme, et `/api/me` repondrait pour le voisin.
    capabilities = public_campus_config(organization_id)["capabilities"]
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

def account_organization(account) -> str:
    """L'organisation d'un compte, jamais celle que le client annoncerait."""
    return (account["organization_id"] if account is not None else None
            ) or nova_organization_id()


def organization_vocabulary(organization_id: str) -> list[dict]:
    """Le vocabulaire distribue par l'organisation, s'il y en a un.

    Passe par le catalogue effectif : une policy fermee ou un package archive
    n'arrive donc pas ici, sans que cette fonction ait a s'en souvenir.
    """
    entries = []
    for package in effective_packages(organization_id)["packages"]:
        if package["type"] != PACKAGE_TYPE_VOCABULARY:
            continue
        entries.extend(package["content"].get("entries", []))
    return entries


def legacy_shared_vocabulary(conn) -> list:
    """Le vocabulaire partage historique — **mode dedie uniquement**.

    `shared_dictionary` n'a jamais porte de colonne `organization_id`. En mode
    dedie, une instance sert une seule organisation : la table lui appartient
    donc entierement, et la lire est sans risque.

    En mode Control Plane, elle appartiendrait a tout le monde a la fois. La
    lire y ferait recevoir a chaque organisation le vocabulaire de ses voisines
    — une fuite silencieuse, d'autant plus difficile a voir qu'elle produirait
    seulement des remplacements inattendus.

    D'ou ce refus explicite plutot qu'une migration : deplacer ces entrees vers
    un package changerait leur comportement sans que personne ne l'ait demande,
    et la bonne trajectoire est qu'une organisation publie son propre package.
    """
    if NOVA_SERVER_MODE != SERVER_MODE_DEDICATED:
        return []
    return conn.execute(
        "SELECT term,replacement FROM shared_dictionary ORDER BY term LIMIT 200"
    ).fetchall()


def writing_context(user: str, organization_id: str | None = None
                    ) -> tuple[str, list[database.Row]]:
    """Return a compact prompt addition and snippets without storing content.

    ## Ordre de composition du vocabulaire

        heritage partage  →  package d'organisation  →  personnel

    **La personne l'emporte en cas de collision.** Un membre qui a corrige un
    terme pour son propre usage l'a fait en connaissance de cause ; le lui
    reprendre a la prochaine publication serait un contenu qui se defait tout
    seul. L'organisation fournit une base, elle ne reecrit pas les choix
    individuels.
    """
    organization_id = organization_id or nova_organization_id()
    with db() as conn:
        shared = legacy_shared_vocabulary(conn)
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
    # Un dictionnaire plutot qu'une concatenation : sans lui, un terme present
    # dans deux sources apparaitrait deux fois dans la consigne, avec deux
    # remplacements differents — et le modele choisirait.
    merged = {}
    for row in shared:
        merged[row["term"].casefold()] = (row["term"], row["replacement"])
    for entry in organization_vocabulary(organization_id):
        merged[entry["term"].casefold()] = (entry["term"], entry["replacement"])
    for row in personal:
        merged[row["term"].casefold()] = (row["term"], row["replacement"])
    vocabulary = [{"term": term, "replacement": replacement}
                  for term, replacement in merged.values()]
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

def apply_snippets(text: str, snippets: list[database.Row]) -> str:
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

@app.get("/api/vocabulary", dependencies=[Depends(product_capability("dictionary", "organization_vocabulary"))])
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

@app.post("/api/dictionary", dependencies=[Depends(product_capability("dictionary", "organization_vocabulary"))])
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

@app.delete("/api/dictionary/{entry_id}", dependencies=[Depends(product_capability("dictionary", "organization_vocabulary"))])
def dictionary_delete(entry_id: int, user: str = Depends(current_user)):
    with db() as conn:
        deleted = conn.execute(
            "DELETE FROM personal_dictionary WHERE id=? AND email=?", (entry_id, user)
        ).rowcount
    if not deleted:
        raise HTTPException(404, "Terme inconnu")
    return {"deleted": True}

@app.post("/api/dictionary/learn", dependencies=[Depends(product_capability("dictionary", "organization_vocabulary"))])
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

@app.get("/api/dictionary/export", response_class=PlainTextResponse, dependencies=[Depends(product_capability("dictionary", "organization_vocabulary"))])
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

@app.post("/api/dictionary/import", dependencies=[Depends(product_capability("dictionary", "organization_vocabulary"))])
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

@app.post("/api/dictionary/analyze", dependencies=[Depends(product_capability("dictionary", "organization_vocabulary"))])
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
        # `RETURNING` plutot que `lastrowid` : SQLite le supporte depuis 3.35 et
        # PostgreSQL depuis toujours, la ou `lastrowid` n'a pas d'equivalent.
        created = conn.execute(
            "INSERT INTO formatting_rules(email,rule,shared) VALUES (?,?,0) RETURNING id",
            (user, rule),
        ).fetchone()
    return {"id": created[0]}

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

@app.get(
    "/api/ai-skills",
    dependencies=[Depends(product_capability("aiSkills", "ai_skills"))],
)
def ai_skills():
    """Les modules AI Skills, si l'organisation les autorise.

    C'est ici que la policy cesse d'etre un affichage. Le poste masque deja
    l'entree de menu quand la capacite est fermee, mais un poste est une
    interface : il peut etre ancien, modifie, ou simplement appeler la route
    directement. Le catalogue vient du serveur — c'est donc au serveur de ne
    pas le donner.

    Le refus est passe au contrat commun `ORGANIZATION_POLICY_DISABLED` en
    Phase 30 : cinq fonctions se refusent desormais pour la meme raison, et
    cinq codes differents auraient oblige la console a les traiter une par une.
    """
    return {"skills": AI_SKILLS}

# --- transcription (proxy vers faster-whisper-server)
#
# ## Deux routes, un seul moteur
#
# La dictee au micro et l'import d'un fichier audio passaient par la meme
# route. Cela paraissait econome, et cela devenait dangereux le jour ou une
# policy d'organisation voudrait fermer l'import : la fermer aurait coupe la
# dictee, c'est-a-dire le produit lui-meme.
#
# D'ou deux routes qui appellent la **meme** fonction interne. Rien n'est
# duplique — ni le moteur, ni le comptage d'usage, ni la gestion d'erreur — et
# les deux usages redeviennent distinguables, ce qu'ils n'auraient jamais du
# cesser d'etre.
#
#     /api/transcribe          dictee micro   — jamais gouvernee par l'import
#     /api/import/transcribe   import fichier — gouvernee par `file_import_enabled`


def runtime_http_error(error: AIRuntimeError, task_type: TaskType) -> HTTPException:
    status_by_code = {
        AI_RUNTIME_BUSY: 429,
        AI_RUNTIME_TIMEOUT: 504,
        AI_RUNTIME_FAILED: 502,
        AI_RUNTIME_UNAVAILABLE: 503,
        AI_RUNTIME_STARTING: 503,
        AI_MODEL_NOT_READY: 503,
        AI_RUNTIME_CONFIG_INVALID: 503,
    }
    detail = {"code": error.code, "task_type": task_type.value}
    if error.retry_after is not None:
        detail["retry_after"] = error.retry_after
    return HTTPException(status_by_code.get(error.code, 502), detail)


async def transcribe_audio(file: UploadFile, user: str, task_type: TaskType) -> dict:
    """Shared speech service; authorization remains route-specific."""
    audio = await file.read()
    try:
        result = await ORGANIZATION_AI_ROUTER.transcribe(
            task_type,
            audio,
            file.filename or "audio.wav",
        )
    except AIRuntimeError as error:
        raise runtime_http_error(error, task_type) from error
    count_usage(user, "transcription", result.duration_seconds or 0.0)
    return {"text": result.text}


@app.post("/api/transcribe")
async def transcribe(file: UploadFile, user: str = Depends(current_user)):
    """Dictee au micro.

    **Aucune policy produit ne ferme cette route.** La dictee est le coeur de
    Nova : une organisation qui la couperait par erreur de configuration
    laisserait ses membres sans recours, et le support ne saurait pas
    distinguer une panne d'une decision. Si ce besoin apparait un jour, il
    demandera son propre examen — connexion, recuperation, hors ligne — pas
    une case cochee par inadvertance dans un panneau d'import.
    """
    return await transcribe_audio(file, user, TaskType.TRANSCRIPTION_REALTIME)


@app.post(
    "/api/import/transcribe",
    dependencies=[Depends(product_capability("fileTranscription", "file_import"))],
)
async def transcribe_import(file: UploadFile, user: str = Depends(current_user)):
    """Import d'un fichier audio deja enregistre.

    Meme moteur que la dictee, meme comptage — seule l'autorisation differe.
    """
    return await transcribe_audio(file, user, TaskType.TRANSCRIPTION_FILE)

async def execute_text(task_type: TaskType, instruction: str, text: str) -> str:
    try:
        result = await ORGANIZATION_AI_ROUTER.generate(task_type, text, instruction)
    except AIRuntimeError as error:
        raise runtime_http_error(error, task_type) from error
    return result.text


def organization_style_instruction(organization_id: str, style_id: str) -> str | None:
    for package in effective_packages(organization_id)["packages"]:
        if package["type"] == PACKAGE_TYPE_STYLE and package["id"] == style_id:
            instruction = package["content"].get("instruction")
            return instruction if isinstance(instruction, str) and instruction.strip() else None
    return None

# --- reformulation (proxy vers LLM, API OpenAI-compatible)
@app.post("/api/reformulate")
async def reformulate(body: ReformulateRequest, user: str = Depends(current_user),
                      account=Depends(current_account)):
    # L'organisation vient du compte : en multi-tenant, l'organisation active de
    # l'instance ne serait pas forcement la sienne.
    context, snippets = writing_context(user, account_organization(account))
    source_text = apply_snippets(body.text, snippets)
    style_id = (body.style_id or "").strip()
    if style_id.startswith(ORGANIZATION_STYLE_PREFIX):
        system_prompt = organization_style_instruction(
            account_organization(account), style_id
        )
        if not system_prompt:
            raise auth_error(404, "STYLE_NOT_AVAILABLE")
        task_type = TaskType.STYLE_TRANSFORM
    else:
        system_prompt = (body.style_prompt or "").strip()
        if not system_prompt:
            system_prompt = (
                "Reformule le texte dicte en un texte clair et professionnel. "
                "Conserve la langue d'origine."
            )
        task_type = TaskType.STYLE_TRANSFORM if style_id else TaskType.REFORMULATION
    if context:
        system_prompt = f"{system_prompt}\n\n{context}"
    result = await execute_text(task_type, system_prompt, source_text)
    count_usage(user, "reformulation")
    return {"text": result}

@app.post("/api/command", dependencies=[Depends(product_capability("commands", "voice_commands"))])
async def command(body: CommandRequest, user: str = Depends(current_user)):
    instruction = clean_required(body.instruction, "instruction", 2000)
    system_prompt = command_prompt(instruction, vocabulary_context(user))
    return {"text": await execute_text(TaskType.VOICE_COMMAND, system_prompt, body.text)}

class SkillRunRequest(BaseModel):
    skill_id: str
    text: str


@app.post("/api/skills/run",
          dependencies=[Depends(product_capability("aiSkills", "ai_skills"))])
async def run_skill(body: SkillRunRequest, user: str = Depends(current_user),
                    account=Depends(current_account)):
    """Execute un AI Skill publie par l'organisation.

    ## Une route distincte, le meme moteur

    `/api/command` fait deja exactement ce travail — une instruction, un texte,
    le modele. Mais elle est gouvernee par `voice_commands_enabled`, et y faire
    passer les AI Skills aurait couple deux policies : fermer les commandes
    vocales aurait ferme les Skills, sans que personne ne l'ait demande.

    D'ou une route qui reutilise `execute_text` et `command_prompt`, et ne
    differe que par son autorisation — le meme choix qu'entre `/api/transcribe`
    et `/api/import/transcribe`.

    ## L'instruction vient du package, jamais du client

    Le poste envoie un **identifiant** et le texte a traiter. S'il envoyait
    l'instruction, n'importe qui pourrait faire executer n'importe quoi au
    modele en se reclamant d'un Skill — et le catalogue publie ne serait plus
    qu'une suggestion.
    """
    organization_id = account_organization(account)
    instruction = None
    for package in effective_packages(organization_id)["packages"]:
        if package["type"] == PACKAGE_TYPE_AI_SKILL and package["id"] == body.skill_id:
            instruction = package["content"].get("instruction")
            break
    if not instruction:
        # Indistinguable d'un identifiant faux : un membre n'a pas a decouvrir
        # ce que son organisation prepare sans l'avoir publie.
        raise auth_error(404, "SKILL_NOT_AVAILABLE")

    system_prompt = command_prompt(instruction, vocabulary_context(user))
    return {"text": await execute_text(TaskType.AI_SKILL, system_prompt, body.text),
            "skill_id": body.skill_id}


@app.post("/api/engineering-notes", dependencies=[Depends(product_capability("engineeringNotes", "engineering_notes"))])
async def engineering_notes(body: CommandRequest, user: str = Depends(current_user),
                            account=Depends(current_account)):
    context, snippets = writing_context(user, account_organization(account))
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
    return {"text": await execute_text(TaskType.ENGINEERING_NOTES, prompt, source_text)}

# ================================================================ ADMIN

# SCIM is installed only after every identity/session guard it delegates to is
# defined.  The protocol module receives an explicit, narrow backend instead of
# importing this module and creating a circular trust boundary.
scim.install_routes(
    app,
    scim.Backend(
        database=db,
        hash_token=hash_token,
        user_by_id=user_by_id,
        user_by_email=user_by_email,
        create_user=create_user,
        update_user_email=update_user_email,
        normalize_email=normalize_email,
        account_status=account_status,
        security_role_for=security_role_for,
        guard_last_identity_manager=guard_last_identity_manager,
        require_organization_access=require_organization_access,
        admin_read_guard=require_admin_capability(CAPABILITY_ORGANIZATION_READ),
        admin_manage_guard=require_admin_capability(CAPABILITY_PROVISIONING_MANAGE),
        rate_limit=enforce_rate_limit,
    ),
)


# Le deploiement d'entreprise s'installe apres les gardes d'administration dont
# il depend, exactement comme SCIM, et pour la meme raison : le module recoit un
# backend explicite plutot que d'importer celui-ci.
deployment.install_routes(
    app,
    deployment.Backend(
        database=db,
        require_organization_access=require_organization_access,
        record_admin_action=record_admin_action,
        admin_read_guard=require_admin_capability(CAPABILITY_DIAGNOSTICS_READ),
        # Le deploiement est une propriete de la machine, pas des personnes :
        # `it_admin` le gere, `organization_admin` ne le gere pas. C'est la
        # frontiere posee en Phase 22, appliquee sans exception ajoutee.
        admin_manage_guard=require_admin_capability(CAPABILITY_DEPLOYMENT_MANAGE),
        rate_limit=enforce_rate_limit,
    ),
)


@app.exception_handler(deployment.DeploymentError)
async def deployment_error_handler(request, error: deployment.DeploymentError):
    # Un code stable, jamais un chemin de fichier ni un detail d'hote : la
    # console doit pouvoir afficher l'erreur sans en apprendre davantage.
    return JSONResponse({"error": error.code}, status_code=error.status)


# ------------------------------------------------------- observabilite HTTP
#
# Un seul middleware : correlation, mesure, en-tetes. Les separer ferait trois
# passages sur chaque requete pour trois lignes de code chacun.

TRUSTED_PROXIES = observability.trusted_proxies_from_environment(
    os.getenv("NOVA_TRUSTED_PROXIES", "")
)


@app.middleware("http")
async def observability_middleware(request, call_next):
    request_id = observability.accept_request_id(
        request.headers.get(observability.REQUEST_ID_HEADER)
    )
    observability.set_request_id(request_id)
    request.state.request_id = request_id
    # L'adresse ne sert qu'a la limitation, jamais a une etiquette de metrique.
    request.state.client_address = observability.client_address(
        request.client.host if request.client else None,
        request.headers.get("X-Forwarded-For"),
        TRUSTED_PROXIES,
    )
    started = time.monotonic()
    route = observability.route_class(request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        observability.METRICS.observe_request(route, 500, time.monotonic() - started)
        observability.log("http.error", level="error", route=route, status=500)
        observability.set_request_id(None)
        raise
    elapsed = time.monotonic() - started
    observability.METRICS.observe_request(route, response.status_code, elapsed)
    if response.status_code >= 500:
        observability.log(
            "http.error", level="error", route=route, status=response.status_code
        )
    response.headers[observability.REQUEST_ID_HEADER] = request_id
    for name, value in observability.security_headers(
        production=NOVA_ENV == "production"
    ).items():
        response.headers.setdefault(name, value)
    observability.set_request_id(None)
    return response


@app.get("/api/livez")
def liveness_probe():
    """Vivacite du processus, et **rien** d'autre.

    `/api/health` existe depuis longtemps et chaque poste l'interroge toutes
    les trente secondes ; il porte les domaines et les methodes d'auth, donc il
    lit la base. C'est utile au poste, mais ce n'est pas une sonde de vivacite :
    un orchestrateur qui l'utiliserait redemarrerait un processus parfaitement
    vivant parce que sa base est momentanement injoignable.

    Cette route ne touche ni la base, ni Redis, ni aucune configuration. Elle
    repond si le processus repond.
    """
    return {"status": "ok"}


@app.get("/api/readyz")
def readiness_probe():
    """Aptitude a servir. Distincte de la vivacite.

    Un orchestrateur a besoin des deux : la vivacite dit s'il faut redemarrer
    le processus, l'aptitude s'il faut lui envoyer du trafic. Les confondre
    fait redemarrer un service parfaitement vivant parce que sa base est
    momentanement injoignable.
    """

    def database_reachable() -> bool:
        with db() as conn:
            conn.execute("SELECT 1")
        return True

    def schema_current() -> bool:
        migrations.check_startup(database_handle())
        return True

    def limiter_available() -> bool:
        limiter = rate_limiter()
        # Sans compteur partage configure, on ne pretend pas etre pret en
        # production : les routes fail-closed refuseraient tout.
        if not limiter.configured:
            return NOVA_ENV != "production"
        return limiter.healthy()

    result = observability.readiness(
        {
            "database": database_reachable,
            "schema": schema_current,
            "rate_limiter": limiter_available,
        }
    )
    return JSONResponse(result, status_code=200 if result["ready"] else 503)


@app.get("/api/metrics")
def metrics_endpoint():
    """Exposition Prometheus.

    Aucune etiquette ne porte d'identifiant : ni utilisateur, ni organisation,
    ni parc, ni connecteur. Une metrique etiquetee par identifiant fabrique une
    serie par identifiant — et publie la liste au passage.
    """
    pool = database_handle().pool_stats
    gauges = {
        "db_pool_idle": pool["idle"],
        "db_pool_leased": pool["leased"],
        "db_pool_max": pool["max"],
        "rate_limiter_shared": 1 if rate_limiter().shared else 0,
    }
    return PlainTextResponse(
        observability.METRICS.render(gauges),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
