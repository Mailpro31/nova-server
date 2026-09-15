-- 0003 organization_learning_lesson_settings
--
-- Ce qu'une organisation decide, lecon par lecon, sur le catalogue Apprendre.
--
-- ## Une surcouche, pas une copie
--
-- Le contenu des lecons reste celui du catalogue livre avec le serveur. Cette
-- table ne porte que les decisions de l'organisation : lecon archivee ou non,
-- obligatoire ou non, echeance, groupes vises. Une lecon sans ligne garde les
-- valeurs neutres — active, facultative, sans echeance, pour tout le monde —
-- si bien qu'une organisation qui n'a rien configure voit exactement le
-- catalogue par defaut.
--
-- ## Archiver n'efface rien
--
-- `status = 'archived'` retire la lecon des membres. La progression, qui vit
-- dans `learning_progress`, n'est pas touchee : restaurer la lecon la rend
-- telle qu'elle etait.
--
-- `group_ids` est un tableau JSON d'identifiants de `organization_groups`.
-- Vide, la lecon vise tout le monde. `revision` evite que deux consoles
-- ouvertes s'ecrasent.
--
-- Dialecte : TEXT, REAL et INTEGER seulement, comme la baseline.

CREATE TABLE organization_learning_lesson_settings(
    organization_id    TEXT NOT NULL,
    lesson_id          TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active',
    required           INTEGER NOT NULL DEFAULT 0,
    due_at             REAL,
    group_ids          TEXT NOT NULL DEFAULT '[]',
    revision           INTEGER NOT NULL DEFAULT 0,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT,
    PRIMARY KEY (organization_id, lesson_id)
);
