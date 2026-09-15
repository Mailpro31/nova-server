-- 0004 organization_learning_custom_lessons
--
-- Les lecons qu'une organisation ecrit elle-meme dans la console.
--
-- ## Le contenu seulement
--
-- Archiver, rendre obligatoire, fixer une echeance, viser des groupes : ces
-- decisions restent dans `organization_learning_lesson_settings`, pour une
-- lecon creee comme pour une lecon par defaut. Cette table ne porte que ce que
-- le catalogue livre porte dans son fichier : le module d'accueil, la duree,
-- la difficulte et les blocs, dans chaque langue fournie.
--
-- `translations` est un objet JSON `{langue: {title, description, blocks}}`.
-- Toutes les langues partagent les memes blocs — memes identifiants, memes
-- types, meme ordre — parce que la progression est enregistree par bloc et non
-- par langue.
--
-- `version` n'avance que lorsque ces blocs changent : corriger une phrase ne
-- remet la progression de personne a zero. `revision` avance a chaque
-- ecriture, pour que deux consoles ouvertes ne s'ecrasent pas.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

CREATE TABLE organization_learning_custom_lessons(
    organization_id    TEXT NOT NULL,
    lesson_id          TEXT NOT NULL,
    module_id          TEXT NOT NULL,
    source_locale      TEXT NOT NULL,
    estimated_minutes  INTEGER NOT NULL,
    difficulty         TEXT NOT NULL,
    translations       TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1,
    revision           INTEGER NOT NULL DEFAULT 1,
    created_at         REAL NOT NULL,
    created_by_user_id TEXT,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT,
    PRIMARY KEY (organization_id, lesson_id)
);
