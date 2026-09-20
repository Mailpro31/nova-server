-- 0010 directory_sync
--
-- La synchronisation d'un annuaire lu par Nova — Google Workspace pour
-- commencer.
--
-- ## Pourquoi une table plutot que des variables d'environnement
--
-- L'adresse de l'administrateur delegue, les groupes retenus et l'etat de la
-- derniere synchronisation se changent depuis la console, sans redemarrer le
-- serveur. Un `.env` obligerait a redeployer pour ajouter une promo.
--
-- ## La cle n'est jamais ici
--
-- `credentials_ref` ne porte qu'une **reference** de secret (`env://…`,
-- `file://…`). La base est sauvegardee, repliquee et restauree ailleurs : une
-- cle de compte de service Google n'y a pas sa place, et le serveur refuse une
-- valeur en clair.
--
-- `last_status` vaut 'ok' ou 'error', avec le code de la derniere erreur. Une
-- synchronisation qui echoue garde l'etat precedent : aucun groupe ne se vide
-- parce que Google n'a pas repondu.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

CREATE TABLE organization_directory_sync(
    organization_id    TEXT NOT NULL,
    provider           TEXT NOT NULL DEFAULT 'google',
    enabled            INTEGER NOT NULL DEFAULT 0,
    admin_subject      TEXT NOT NULL DEFAULT '',
    customer_id        TEXT NOT NULL DEFAULT 'my_customer',
    credentials_ref    TEXT NOT NULL DEFAULT '',
    group_filter       TEXT NOT NULL DEFAULT '[]',
    last_sync_at       REAL,
    last_status        TEXT,
    last_error         TEXT,
    last_outcome       TEXT,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT,
    PRIMARY KEY (organization_id, provider)
);
