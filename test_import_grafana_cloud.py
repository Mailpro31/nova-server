import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "scripts"))
from import_grafana_cloud import cloud_dashboard  # noqa: E402


def test_every_dashboard_points_at_grafana_cloud_sources():
    folder = pathlib.Path(__file__).resolve().parent / "panel/grafana/dashboards"
    for path in folder.glob("*.json"):
        text = str(cloud_dashboard(path.read_text(encoding="utf-8")))
        assert "nova-prometheus" not in text and "nova-loki" not in text, path.name
