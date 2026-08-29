-- 0002 learning_progress
--
-- Progression Learn, par membre et par lecon.
--
-- ## Ce que cette table contient, et ce qu'elle ne contiendra jamais
--
-- Elle porte de la metadonnee pedagogique : ou en est quelqu'un, sur quelle
-- version de la lecon, depuis quand. Elle ne porte aucun texte libre — ni la
-- reponse ecrite dans un exercice, ni le retour du modele. Un exercice se
-- traite et se jette ; le conserver aurait fait de Learn le seul endroit de
-- Nova ou l'on garde ce que quelqu'un a ecrit.
--
-- `completed_blocks` est un tableau JSON d'identifiants de blocs. Une table
-- fille aurait ete plus orthodoxe, mais ces identifiants ne sont jamais
-- interroges autrement que tous ensemble, pour une seule lecon : la jointure
-- n'aurait servi personne.
--
-- ## Portee de tenant
--
-- `organization_id` est dans la cle d'unicite, pas seulement dans la ligne. La
-- meme adresse peut exister dans deux organisations — c'est deja vrai pour
-- `users` — et deux progressions distinctes doivent alors le rester.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

CREATE TABLE learning_progress(
    progress_id      TEXT PRIMARY KEY,
    organization_id  TEXT NOT NULL,
    email            TEXT NOT NULL,
    lesson_id        TEXT NOT NULL,
    status           TEXT NOT NULL,
    lesson_version   INTEGER NOT NULL,
    completed_blocks TEXT NOT NULL DEFAULT '[]',
    last_block_id    TEXT,
    started_at       REAL,
    updated_at       REAL NOT NULL,
    completed_at     REAL
);

CREATE UNIQUE INDEX idx_learning_progress_member_lesson
    ON learning_progress(organization_id, email, lesson_id);

CREATE INDEX idx_learning_progress_member
    ON learning_progress(organization_id, email);
