"""Importe les tableaux et les alertes du panel dans Grafana Cloud.

Usage : GRAFANA_URL=https://<pile>.grafana.net GRAFANA_TOKEN_FILE=<fichier> \
    python scripts/import_grafana_cloud.py

Le jeton (compte de service, rôle Editor) est lu dans un fichier, jamais affiché.
Les sources locales deviennent celles que Grafana Cloud crée d'office.
"""

import json
import os
import pathlib
import urllib.request

import yaml

SOURCES = {"nova-prometheus": "grafanacloud-prom", "nova-loki": "grafanacloud-logs"}


def cloud_dashboard(text: str) -> dict:
    for local, cloud in SOURCES.items():
        text = text.replace(f'"{local}"', f'"{cloud}"')
    return {"dashboard": json.loads(text), "overwrite": True}


def alert_groups(text: str) -> list[dict]:
    """Les groupes de panel/prometheus/alerts.yml, tels que le ruler de Mimir les attend."""
    return yaml.safe_load(text)["groups"]


def post(url: str, token: str, body: dict) -> int:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return response.status


def main() -> None:
    base = os.environ["GRAFANA_URL"].rstrip("/")
    token = pathlib.Path(os.environ["GRAFANA_TOKEN_FILE"]).read_text().strip()
    panel = pathlib.Path(__file__).resolve().parent.parent / "panel"
    for path in sorted((panel / "grafana/dashboards").glob("*.json")):
        body = cloud_dashboard(path.read_text(encoding="utf-8"))
        body["dashboard"].pop("id", None)
        print(path.name, post(base + "/api/dashboards/db", token, body))
    # Les règles vont dans le ruler de la source Prometheus du cloud (espace « nova »).
    rules_url = base + f"/api/ruler/{SOURCES['nova-prometheus']}/api/v1/rules/nova"
    for group in alert_groups((panel / "prometheus/alerts.yml").read_text(encoding="utf-8")):
        print("alertes", group["name"], post(rules_url, token, group))


if __name__ == "__main__":
    main()
