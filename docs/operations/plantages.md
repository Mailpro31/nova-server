# Plantages du poste (Sentry / GlitchTip)

Le poste Nova embarque un rapport de plantage **dormant** : tant que le DSN est
vide, rien ne part. Aucun compte n'est créé ici et aucune clé n'est dans le dépôt.

## Où coller le DSN

1. Créer un projet Sentry (ou GlitchTip, compatible) de type Rust et copier son DSN
   (`https://<clé>@<hôte>/<projet>`). Un DSN permet d'envoyer seulement : il est
   prévu pour être embarqué côté client.
2. Dépôt `Mailpro31/nova-desktop`, Settings > Secrets and variables > Actions :
   le secret **`NOVA_SENTRY_DSN`** existe déjà. Y coller le DSN.
3. `.github/workflows/build.yml` le passe à la compilation (`option_env!`,
   voir `src-tauri/src/telemetry.rs`). Il est lu au build : publier une nouvelle
   version pour qu'il prenne effet. Pour un essai local : `NOVA_SENTRY_DSN=... bun run tauri dev`.

## Vérifier

- Lancer le poste avec le DSN, provoquer un plantage de test, puis ouvrir le
  projet Sentry : l'événement apparaît avec la version (`release`) du poste.
- Secret vide : aucun trafic sortant vers Sentry (état normal et attendu).
- Le texte dicté et les adresses ne doivent jamais y figurer ; en cas de doute,
  ouvrir un événement et le signaler avant de continuer.

## Rapport « Signaler un problème »

Indépendant de Sentry : le bouton du poste envoie `POST /api/support/report` au
serveur de l'école, qui journalise `support.report` en **warning**. Alloy ne
transmet que warning et error : le rapport arrive dans Loki/Grafana chez
l'éditeur. Dans Grafana : `{level="warning"} | json | event="support.report"`.
Contenu : version, système, mémoire, état des moteurs, dernier code d'erreur,
horodatage, description facultative (500 caractères, adresses masquées),
identifiant opaque. Limite : 5 rapports par heure et par compte.
