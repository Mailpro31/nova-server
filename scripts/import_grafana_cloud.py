"""Importe les tableaux du panel (panel/grafana/dashboards) dans Grafana Cloud.

Usage : GRAFANA_URL=https://<pile>.grafana.net GRAFANA_TOKEN_FILE=<fichier> \
    python scripts/import_grafana_cloud.py

Le jeton (compte de service, rôle Editor) est lu dans un fichier, jamais affiché.
Les sources locales deviennent celles que Grafana Cloud crée d'office.
"""

import json
import os
import pathlib
import urllib.request

SOURCES = {"nova-prometheus": "grafanacloud-prom", "nova-loki": "grafanacloud-logs"}


def cloud_dashboard(text: str) -> dict:
    for local, cloud in SOURCES.items():
        text = text.replace(f'"{local}"', f'"{cloud}"')
    return {"dashboard": json.loads(text), "overwrite": True}


def main() -> None:
    url = os.environ["GRAFANA_URL"].rstrip("/") + "/api/dashboards/db"
    token = pathlib.Path(os.environ["GRAFANA_TOKEN_FILE"]).read_text().strip()
    folder = pathlib.Path(__file__).resolve().parent.parent / "panel/grafana/dashboards"
    for path in sorted(folder.glob("*.json")):
        body = cloud_dashboard(path.read_text(encoding="utf-8"))
        body["dashboard"].pop("id", None)
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            print(path.name, response.status)


if __name__ == "__main__":
    main()
