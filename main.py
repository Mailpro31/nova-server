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
import hmac
import ipaddress
import json
import secrets
import socket
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from urllib.parse import urlencode, urlparse

import httpx
import jwt
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

# --- Google Workspace : même flux, contraintes propres --------------------
#
# Google émet un secret pour ses clients « Desktop » et l'exige à l'échange du
# code, tout en documentant qu'une application installée « ne peut pas garder de
# secret ». Ce n'est donc pas un secret au sens cryptographique : c'est un
# identifiant public que le protocole réclame. Il reste malgré tout côté
# serveur — jamais dans Nova.exe — parce que c'est le serveur qui échange le
# code. Le poste demeure un client public au sens strict.
GOOGLE_CLIENT_ID = os.getenv("NOVA_GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("NOVA_GOOGLE_CLIENT_SECRET", "").strip()
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
OIDC_CLIENT_SECRET = os.getenv("NOVA_OIDC_CLIENT_SECRET", "").strip()
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

# Mode de serveur. `dedicated` sert une organisation — c'est le mode déployé
# aujourd'hui. `control_plane` en servira plusieurs : il n'existe pas encore
# complètement, mais la distinction doit exister **avant** que du code suppose
# partout qu'une seule organisation existe.
SERVER_MODE_DEDICATED = "dedicated"
SERVER_MODE_CONTROL_PLANE = "control_plane"
NOVA_SERVER_MODE = os.getenv("NOVA_SERVER_MODE", SERVER_MODE_DEDICATED).strip().lower()
if NOVA_SERVER_MODE not in {SERVER_MODE_DEDICATED, SERVER_MODE_CONTROL_PLANE}:
    NOVA_SERVER_MODE = SERVER_MODE_DEDICATED

# Le jeton d'administration partagé est **hérité**. Il authentifie une machine,
# pas une personne : il ne peut ni être révoqué individuellement, ni figurer
# dans un journal d'audit autrement que comme « quelqu'un qui le détenait ».
# Il reste accepté en mode dédié pour ne pas casser les déploiements existants,
# et jamais en mode Control Plane, où il autoriserait un porteur à administrer
# des organisations qui ne sont pas la sienne.
LEGACY_ADMIN_TOKEN_ENABLED = (
    os.getenv("NOVA_LEGACY_ADMIN_TOKEN", "true").strip().lower() != "false"
    and NOVA_SERVER_MODE == SERVER_MODE_DEDICATED
)

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
        -- État d'authentification antérieur à l'identité : à ce stade, seule
        -- l'adresse est connue. Elle est donc cadrée par l'organisation.
        CREATE TABLE IF NOT EXISTS auth_codes(
            email TEXT, code TEXT, expires_at REAL, attempts INTEGER DEFAULT 0,
            organization_id TEXT
        );
        -- Sessions Nova. Rattachées à `user_id` : l'e-mail n'est qu'un repère
        -- lisible, et changer d'adresse ne doit déconnecter personne.
        CREATE TABLE IF NOT EXISTS tokens(
            token TEXT PRIMARY KEY, email TEXT, machine TEXT,
            user_id TEXT, organization_id TEXT,
            active INTEGER DEFAULT 1, created_at REAL, expires_at REAL
        );
        -- Comptes. `user_id` est l'identité stable ; l'adresse e-mail n'est
        -- qu'un attribut, unique **dans une organisation** et non globalement.
        -- Voir `docs/architecture/multi-tenant-user-identity.md`.
        CREATE TABLE IF NOT EXISTS users(
            user_id TEXT PRIMARY KEY,          -- UUID Nova, attribué une fois
            organization_id TEXT NOT NULL,
            email TEXT NOT NULL,               -- tel que saisi : casse d'affichage
            normalized_email TEXT NOT NULL,    -- forme de recherche et d'unicité
            role TEXT DEFAULT 'student',       -- student | teacher | staff | partner
            security_role TEXT NOT NULL DEFAULT 'member',
            cohort TEXT DEFAULT '',            -- ex. "Promo 2028"
            note TEXT DEFAULT '',              -- note libre de l'admin
            disabled INTEGER DEFAULT 0,        -- compte suspendu
            status TEXT DEFAULT 'active',
            identity_provider TEXT,
            external_subject TEXT,
            external_tenant_id TEXT,
            created_at REAL,
            updated_at REAL,
            last_seen REAL
        );
        -- L'index unique (organization_id, normalized_email) est créé par
        -- `migrate_schema` : sur une base antérieure, les deux colonnes
        -- n'existent pas encore à cet instant.
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
            google_customer_id TEXT,           -- Admin SDK uniquement, non lu aujourd'hui
            google_hosted_domain TEXT,         -- revendication `hd`, mapping explicite
            oidc_issuer TEXT,
            -- Découverte : comment un poste trouve cette organisation.
            discovery_slug TEXT,               -- code public, pas un secret
            discovery_enabled INTEGER NOT NULL DEFAULT 0,
            deployment_mode TEXT NOT NULL DEFAULT 'dedicated',
            service_endpoint TEXT,             -- adresse annoncée au poste
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL
        );
        -- Tentatives de connexion SSO. En base plutôt qu'en mémoire de
        -- processus : `start` et `exchange` sont deux appels séparés par un
        -- aller-retour dans le navigateur, et rien ne garantit qu'ils
        -- atterrissent sur le même worker.
        CREATE TABLE IF NOT EXISTS sso_flows(
            flow_id_hash TEXT PRIMARY KEY,     -- empreinte, jamais l'identifiant
            provider_type TEXT NOT NULL,
            provider_config_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            nonce_hash TEXT NOT NULL,          -- empreinte : jamais relu en clair
            redirect_uri TEXT NOT NULL,
            machine TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            exchanging_at REAL,
            consumed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_sso_flows_expiry ON sso_flows(expires_at);

        -- Configurations de fournisseur, rattachées à une organisation. Ce qui
        -- était global à l'instance devient une ligne par organisation, avec
        -- son propre identifiant immuable — condition pour que plusieurs IdP
        -- OIDC coexistent, et pour le futur Control Plane.
        CREATE TABLE IF NOT EXISTS organization_provider_configs(
            id TEXT PRIMARY KEY,               -- UUID Nova, immuable
            organization_id TEXT NOT NULL,
            provider_type TEXT NOT NULL,       -- microsoft_entra | google_workspace | oidc
            display_name TEXT,                 -- libellé du bouton, affichage seul
            enabled INTEGER NOT NULL DEFAULT 1,
            -- Rattachement, par type. Structuré plutôt qu'un JSON opaque :
            -- une colonne se valide, s'indexe et se lit.
            entra_tenant_id TEXT,
            google_hosted_domain TEXT,
            oidc_issuer TEXT,
            oidc_client_id TEXT,
            -- Secret côté serveur uniquement : jamais renvoyé par une API.
            oidc_client_secret TEXT,
            oidc_client_auth_method TEXT DEFAULT 'none',
            oidc_allow_private_issuer INTEGER NOT NULL DEFAULT 0,
            oidc_required_claim TEXT,
            oidc_required_claim_value TEXT,
            created_at REAL,
            updated_at REAL,
            -- Nullables : Nova Admin les remplira, il n'existe pas encore.
            created_by TEXT,
            updated_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_provider_configs_org
            ON organization_provider_configs(organization_id, provider_type);
        -- Un même émetteur ne peut pas être déclaré deux fois pour une
        -- organisation : ce serait deux chemins vers la même identité.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_configs_issuer
            ON organization_provider_configs(organization_id, provider_type, oidc_issuer);

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
            -- Émetteur, pour l'OIDC générique. Vide chez Microsoft et Google,
            -- dont le fournisseur suffit à cadrer le sujet.
            external_issuer TEXT NOT NULL DEFAULT '',
            created_at REAL,
            last_seen REAL,
            -- L'émetteur fait partie de la clé : deux IdP OIDC distincts
            -- peuvent parfaitement attribuer le même `sub` à deux personnes
            -- différentes. Sans lui, la seconde écraserait la première.
            PRIMARY KEY (provider, external_issuer, external_subject)
        );

        -- Sessions d'administration. Séparées des sessions utilisateur, et
        -- c'est le point du design : être administrateur ne doit pas rendre
        -- chaque session de dictée quotidienne capable de reconfigurer
        -- l'organisation. Le privilège s'ouvre explicitement, pour une durée
        -- courte, et se referme.
        CREATE TABLE IF NOT EXISTS admin_sessions(
            id TEXT PRIMARY KEY,               -- UUID, sert d'identifiant d'audit
            user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            -- Rôle au moment de l'ouverture. **Indicatif** : il est revérifié
            -- en base à chaque appel. Une session ne fige pas un privilège.
            security_role TEXT NOT NULL,
            auth_provider TEXT,
            token_hash TEXT NOT NULL,          -- empreinte seule, comme les sessions Nova
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,          -- expiration absolue
            last_seen_at REAL,                 -- sert l'expiration d'inactivité
            revoked_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_sessions_user
            ON admin_sessions(user_id, revoked_at);

        -- Journal d'audit des actions d'administration. Ajout seul : aucune
        -- route ne le modifie ni ne le supprime, et les horodatages viennent du
        -- serveur. SQLite n'offre aucune immuabilité au niveau du stockage —
        -- quiconque tient le fichier tient le journal.
        CREATE TABLE IF NOT EXISTS admin_audit_log(
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            actor_user_id TEXT,                -- absent pour le jeton hérité
            actor_role TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            request_id TEXT,
            -- Métadonnées non sensibles uniquement : jamais un secret, jamais
            -- un jeton, jamais de contenu dicté.
            metadata TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_audit_org
            ON admin_audit_log(organization_id, created_at);
        """)
        migrate_schema(conn)

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
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
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
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
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
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
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
                PRIMARY KEY (provider, external_issuer, external_subject)
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
        conn.execute(
            "INSERT OR REPLACE INTO server_state(key, value) VALUES (?,?)",
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
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
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
                "organization_id, nonce_hash, redirect_uri, machine, status, "
                "created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    hash_token(flow_id),
                    provider_type,
                    provider_config_id,
                    organization_id,
                    hash_token(nonce),
                    redirect_uri,
                    machine,
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




init_db()

# Le jeton d'administration partagé est une dette, pas une fonctionnalité. Un
# déploiement qui s'y appuie encore hors développement doit le savoir en clair
# au démarrage, plutôt que de le découvrir le jour où il faudra le révoquer.
if LEGACY_ADMIN_TOKEN_ENABLED and NOVA_ENV != "development":
    print(
        "[admin] WARNING legacy X-Admin-Token enabled — deprecated, "
        "authenticates a machine and not a person; set NOVA_LEGACY_ADMIN_TOKEN=false "
        "once administrators have Nova security roles"
    )
print(f"[admin] server_mode={NOVA_SERVER_MODE} legacy_admin_token={LEGACY_ADMIN_TOKEN_ENABLED}")


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
    }),
    SECURITY_ROLE_IT_ADMIN: frozenset({
        CAPABILITY_ORGANIZATION_READ,
        CAPABILITY_PROVIDER_MANAGE,
        CAPABILITY_DISCOVERY_MANAGE,
        CAPABILITY_DEPLOYMENT_MANAGE,
        CAPABILITY_DIAGNOSTICS_READ,
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
            "external_tenant_id, organization_id, email, verification, external_issuer, "
            "created_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                provider,
                external_subject,
                external_tenant_id,
                organization_id,
                email,
                verification,
                external_issuer,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE federated_identities SET last_seen=?, email=?, external_tenant_id=?, "
            "verification=? WHERE provider=? AND external_issuer=? AND external_subject=?",
            (now, email, external_tenant_id, verification, provider, external_issuer,
             external_subject),
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

def require_admin(x_admin_token: str = Header(default="")):
    """Jeton d'administration partagé — **hérité**, conservé pour compatibilité.

    Voir `LEGACY_ADMIN_TOKEN_ENABLED` : refusé en mode Control Plane, où il
    autoriserait un porteur à administrer une organisation qui n'est pas la
    sienne. Les nouvelles routes utilisent `require_admin_capability`.
    """
    if not LEGACY_ADMIN_TOKEN_ENABLED:
        raise auth_error(403, "ADMIN_REQUIRED")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(403, "Admin invalide")
    return True


# ─────────────────────────── Administration moderne ───────────────────────
#
# ## Ce que cette section remplace
#
# `X-Admin-Token` authentifie **une machine, pas une personne**. Il ne se révoque
# pas individuellement, ne dit pas qui a agi, ne distingue aucun rôle, et le même
# secret circule entre tous ceux qui administrent. Un journal d'audit alimenté
# par lui ne peut écrire qu'une chose : « quelqu'un qui détenait le jeton ».
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

    @property
    def is_legacy_token(self) -> bool:
        return self.session_id is None


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
):
    """Écrit une ligne d'audit. Ajout seul.

    `metadata` ne doit porter que des faits non sensibles — un type de
    fournisseur, un booléen, un mode. Jamais un secret, un jeton, un code
    d'autorisation, un `id_token`, un `nonce`, ni le moindre contenu dicté. Ce
    que l'on n'écrit pas ne peut pas fuir par un journal.
    """
    with db() as conn:
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


def admin_principal(
    authorization: str = Header(default=""),
    x_admin_token: str = Header(default=""),
) -> AdminPrincipal:
    """Identifie l'administrateur d'un appel.

    Deux chemins, dans cet ordre : la session d'administration moderne, puis le
    jeton hérité s'il est encore permis. Le client n'annonce jamais « ce jeton
    est un jeton d'administration » — le serveur le sait par sa table. Un jeton
    de session utilisateur présenté ici est simplement introuvable dans
    `admin_sessions`, donc refusé : la séparation est stricte.
    """
    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token:
            return resolve_admin_session(token)
    if x_admin_token and LEGACY_ADMIN_TOKEN_ENABLED:
        if not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
            raise auth_error(403, "ADMIN_REQUIRED")
        # Le jeton hérité ne porte aucune identité. Il est cadré à
        # l'organisation servie par cette instance, et à elle seule.
        return AdminPrincipal(
            user_id=None,
            email=None,
            organization_id=nova_organization_id(),
            security_role=SECURITY_ROLE_ORGANIZATION_ADMIN,
            session_id=None,
            auth_provider="legacy_admin_token",
            issued_at=time.time(),
        )
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


def organization_for_provider(provider: str) -> sqlite3.Row | None:
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


def organization_for_tenant_legacy(provider: str, tenant_value: str) -> sqlite3.Row | None:
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
            "WHERE provider=? AND external_issuer=? AND external_subject=?",
            (provider, external_issuer, external_subject),
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


async def start_sso_flow(provider: str, body: SsoStartRequest) -> dict:
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
# Le strict nécessaire pour éviter de manipuler SQLite à la main. Ce n'est pas
# Nova Admin : pas d'interface, pas de gestion d'utilisateurs, pas de journal
# d'audit. L'authentification est celle qui existe déjà — `X-Admin-Token` — et
# la portée est l'organisation servie par cette instance, jamais une autre.


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
        except sqlite3.IntegrityError:
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


@app.post("/api/admin/session")
def open_admin_session(user: str = Depends(current_user)):
    """Ouvre une session d'administration depuis une session Nova valide.

    Aucun client ne demande un rôle : il présente son jeton utilisateur, et le
    serveur regarde en base ce que cette personne est autorisée à faire. Un
    membre ordinaire obtient un refus, quoi que raconte son IdP.

    ## Dette assumée : pas de step-up

    Idéalement, ouvrir une session d'administration exigerait une
    authentification récente ou un second facteur au moment de l'élévation.
    Nova ne peut pas l'offrir honnêtement aujourd'hui : le second facteur vit
    chez l'IdP, et rien dans le flux actuel ne prouve à Nova qu'il vient d'être
    présenté. Plutôt que de simuler un MFA que nous ne contrôlons pas, la
    session est **courte**, ouverte depuis une session utilisateur valide, et
    revérifiée à chaque appel. Le step-up réel — `max_age` / `prompt=login` puis
    lecture de `auth_time` — est documenté comme dette.
    """
    with db() as conn:
        row = user_by_email(conn, nova_organization_id(), user)
    if row is None:
        raise auth_error(403, "ADMIN_ROLE_REQUIRED")
    role = security_role_for(row)
    if role not in ADMIN_SECURITY_ROLES:
        # Même refus pour un membre inconnu et pour un membre connu sans rôle :
        # « existe mais n'est pas administrateur » n'est une information utile
        # qu'à celui qui sonde.
        raise auth_error(403, "ADMIN_ROLE_REQUIRED")
    if account_status(row) != "active":
        raise auth_error(403, "ADMIN_ROLE_REQUIRED")

    session = create_admin_session(row, row["identity_provider"])
    principal = AdminPrincipal(
        user_id=row["user_id"],
        email=row["email"],
        organization_id=row["organization_id"] or nova_organization_id(),
        security_role=session["role"],
        session_id=session["session_id"],
        auth_provider=row["identity_provider"],
        issued_at=time.time(),
    )
    record_admin_action(
        principal, "admin_session.create",
        target_type="admin_session", target_id=session["session_id"],
    )
    # Le jeton n'est jamais journalisé, ici pas plus qu'ailleurs.
    print(f"[admin] session opened role={session['role']}")
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
def admin_overview(
    _: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_DIAGNOSTICS_READ)),
):
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
def admin_user_detail(
    email: str,
    _: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Profil complet : infos + appareils + historique d'usage (30 jours, compteurs)."""
    email = email.lower()
    with db() as conn:
        u = user_by_email(conn, nova_organization_id(), email)
        if not u:
            raise HTTPException(404, "Utilisateur inconnu")
        machines = conn.execute(
            "SELECT machine, active, created_at FROM tokens WHERE user_id=?",
            (u["user_id"],)
        ).fetchall()
        history = conn.execute(
            "SELECT day, transcriptions, reformulations, audio_seconds FROM usage "
            "WHERE email=? ORDER BY day DESC LIMIT 30", (email,)
        ).fetchall()
    return {"profile": dict(u),
            "machines": [dict(m) for m in machines],
            "usage_30d": [dict(h) for h in history]}

@app.post("/api/admin/user/{email}")
def admin_user_update(
    email: str,
    body: UserUpdate,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Rôle métier, cohorte, note, suspension.

    Ce que cette route ne peut **pas** faire : attribuer un rôle de sécurité.
    `users.role` décrit un métier ; le privilège vit dans `users.security_role`,
    qu'aucun endpoint n'écrit — voir `set_security_role` et `admin_cli.py`.
    """
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
    organization_id = nova_organization_id()
    with db() as conn:
        existing = user_by_email(conn, organization_id, email)
        if existing is None:
            # Créer un compte depuis une route d'administration reste possible,
            # mais il naît désormais avec une identité, pas avec une adresse.
            create_user(conn, organization_id, email)
            existing = user_by_email(conn, organization_id, email)
        conn.execute(
            f"UPDATE users SET {', '.join(fields)}, updated_at=? WHERE user_id=?",
            (*vals, time.time(), existing["user_id"]),
        )
    record_admin_action(
        principal, "user.update", target_type="user", target_id=email,
        metadata={"fields": sorted(f.rstrip("=?") for f in fields)},
    )
    return {"updated": True}

@app.post("/api/admin/user/{email}/revoke-machines")
def admin_revoke_machines(
    email: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Déconnecte l'utilisateur de tous ses appareils (il devra refaire un code email)."""
    with db() as conn:
        existing = user_by_email(conn, nova_organization_id(), email)
        if existing is None:
            raise HTTPException(404, "Utilisateur inconnu")
        # Par `user_id` : révoquer les appareils d'une personne ne doit jamais
        # atteindre son homonyme dans une autre organisation.
        n = conn.execute("UPDATE tokens SET active=0 WHERE user_id=?",
                         (existing["user_id"],)).rowcount
    record_admin_action(
        principal, "user.revoke_machines", target_type="user", target_id=email.lower())
    return {"revoked_tokens": n}

@app.delete("/api/admin/user/{email}")
def admin_user_delete(
    email: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Supprime complètement l'utilisateur (profil, tokens, compteurs)."""
    email = email.lower()
    organization_id = nova_organization_id()
    with db() as conn:
        # Jamais `DELETE ... WHERE email=?` seul : cela effacerait aussi
        # l'homonyme d'une autre organisation.
        existing = user_by_email(conn, organization_id, email)
        if existing is None:
            raise HTTPException(404, "Utilisateur inconnu")
        user_id = existing["user_id"]
        conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM usage WHERE user_id=?", (user_id,))
        conn.execute(
            "DELETE FROM auth_codes WHERE lower(trim(email))=? AND organization_id=?",
            (normalize_email(email), organization_id),
        )
    record_admin_action(principal, "user.delete", target_type="user", target_id=email)
    return {"deleted": email}

@app.post("/api/admin/cohort/{cohort}/disable")
def admin_cohort_disable(
    cohort: str,
    principal: AdminPrincipal = Depends(require_admin_capability(CAPABILITY_IDENTITY_MANAGE)),
):
    """Suspend toute une cohorte (ex. fin d'année scolaire)."""
    with db() as conn:
        n = conn.execute("UPDATE users SET disabled=1 WHERE cohort=?", (cohort,)).rowcount
    record_admin_action(
        principal, "cohort.disable", target_type="cohort", target_id=cohort,
        metadata={"affected": n})
    return {"disabled_users": n}

# ---------------------------------------------------------------- admin UI
@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return (Path(__file__).parent / "admin.html").read_text(encoding="utf-8")
