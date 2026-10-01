-- 0011 usage_metrics
--
-- L'historique dont la console a besoin pour tracer des courbes : l'usage
-- heure par heure, la latence ressentie, et l'etat de la machine.
--
-- ## Pourquoi trois tables et pas une
--
-- Elles n'ont ni la meme granularite ni la meme duree de vie. `usage_hourly`
-- porte le detail que l'on garde 90 jours ; `usage_monthly` la trace longue
-- que l'on garde deux ans ; `host_samples` un releve machine qui n'a pas de
-- proprietaire et se purge sur le meme rythme que le detail.
--
-- Les fusionner obligerait a garder la granularite la plus fine pendant la
-- duree la plus longue, ou a perdre les pics.
--
-- ## Pourquoi `email` comme cle, et pas `user_id`
--
-- Pour rester coherent avec la table `usage` deja en place, dont le chemin
-- d'ecriture ne connait que l'adresse. Introduire une cle differente ici
-- obligerait a faire descendre un identifiant de plus a travers tout l'appel
-- de dictee, pour un gain nul : les deux designent la meme personne.
--
-- ## La latence est stockee en somme et en compte, jamais en moyenne
--
-- Une moyenne deja calculee ne se re-agrege pas : la moyenne de moyennes
-- horaires n'est pas la moyenne de la journee des que les heures n'ont pas le
-- meme nombre de dictees. La somme et le compte, si. `latency_ms_max` garde le
-- pire cas, que l'objectif de cinq secondes rend plus parlant que la moyenne.
--
-- ## Ce qui n'est pas ici
--
-- Aucun contenu dicte, aucun texte produit. Ces tables comptent des
-- evenements et mesurent des durees ; elles ne conservent rien de ce que les
-- gens ecrivent.

CREATE TABLE usage_hourly(
    email            TEXT NOT NULL,
    hour             TEXT NOT NULL,
    transcriptions   INTEGER NOT NULL DEFAULT 0,
    reformulations   INTEGER NOT NULL DEFAULT 0,
    audio_seconds    REAL NOT NULL DEFAULT 0,
    latency_ms_sum   REAL NOT NULL DEFAULT 0,
    latency_ms_count INTEGER NOT NULL DEFAULT 0,
    latency_ms_max   REAL NOT NULL DEFAULT 0,
    user_id          TEXT,
    organization_id  TEXT,
    PRIMARY KEY (email, hour)
);

CREATE INDEX idx_usage_hourly_hour ON usage_hourly(hour);

CREATE TABLE usage_monthly(
    email            TEXT NOT NULL,
    month            TEXT NOT NULL,
    transcriptions   INTEGER NOT NULL DEFAULT 0,
    reformulations   INTEGER NOT NULL DEFAULT 0,
    audio_seconds    REAL NOT NULL DEFAULT 0,
    latency_ms_sum   REAL NOT NULL DEFAULT 0,
    latency_ms_count INTEGER NOT NULL DEFAULT 0,
    latency_ms_max   REAL NOT NULL DEFAULT 0,
    organization_id  TEXT,
    PRIMARY KEY (email, month)
);

CREATE INDEX idx_usage_monthly_month ON usage_monthly(month);

CREATE TABLE host_samples(
    sampled_at        TEXT NOT NULL,
    cpu_percent       REAL,
    ram_used_gb       REAL,
    ram_total_gb      REAL,
    gpu_percent       REAL,
    vram_used_gb      REAL,
    vram_total_gb     REAL,
    gpu_temperature_c REAL,
    gpu_power_w       REAL,
    PRIMARY KEY (sampled_at)
);
