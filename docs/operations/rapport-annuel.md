# Rapport d'usage annuel d'une école

PDF d'une ou deux pages, à envoyer à l'école au renouvellement. Il lit les séries
que l'agent du panel envoie déjà à Grafana Cloud (étiquette `school`), sans
toucher au serveur de l'école.

## Ce qu'il faut

1. **URL Prometheus et utilisateur** : Grafana Cloud > My Account > la pile Nova >
   tuile *Prometheus* > Details. Copier *Remote Write Endpoint* sans le chemin
   `/push` pour obtenir l'URL de requête (`https://prometheus-prod-XX.grafana.net/api/prom`),
   et le champ *Username / Instance ID* (un nombre).
2. **Jeton en lecture seule, à créer par Sash** : Grafana Cloud > Access Policies >
   Create access policy, portée **`metrics:read`** uniquement (aucune écriture, aucune
   autre portée), puis Add token. Le jeton d'écriture de l'agent (`NOVA_PANEL_TOKEN`)
   ne doit pas servir ici. Enregistrer le jeton dans un fichier hors dépôt.
3. `pip install -r scripts/requirements-report.txt` (fpdf2, pur Python).

## Lancer

```bash
export GRAFANA_PROM_URL=https://prometheus-prod-XX.grafana.net/api/prom
export GRAFANA_PROM_USER=123456
export GRAFANA_PROM_TOKEN_FILE=~/secrets/grafana-lecture.token
python scripts/annual_report.py --school <slug> --from 2025-11-01 --to 2026-10-31 --out rapport.pdf
```

`<slug>` est la valeur de `NOVA_PANEL_SCHOOL` de l'école. La période va jusqu'à
13 mois (requête horaire pour la disponibilité).

## Contenu et limites

| Section | Série | Remarque |
|---|---|---|
| Membres actifs | `nova_members{state="active"}` | dernière valeur du mois |
| Utilisateurs du mois | `nova_active_users{period="month"}` | maximum du mois |
| Dictées | `nova_stage_seconds_count{stage="transcription"}` | `increase` quotidien, estimation |
| Fonctions utilisées | `nova_stage_seconds_count` par étape | pas de série par fonction : étapes de traitement |
| Enveloppe d'IA | `nova_ai_envelope_eur`, `nova_ai_envelope_percent` | dernière valeur |
| Disponibilité | `nova_uptime_seconds` | part des heures avec une mesure reçue (inclut une panne de l'agent) |
| Heures d'audio | aucune | toujours « non mesuré » |

Une série absente donne « non mesuré ». Un mois dont l'effectif est inconnu ou
inférieur à 5 n'est pas chiffré. Aucun nom ni aucune adresse : les séries n'en portent pas.
Le jeton, l'URL et l'utilisateur ne sont jamais affichés.
