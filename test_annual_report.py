"""Rapport annuel : PDF produit, totaux justes, « non mesuré », jamais d'adresse."""

import datetime as dt
import io
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import annual_report  # noqa: E402

START, END = dt.date(2026, 1, 1), dt.date(2026, 3, 31)
DAY = 86400
T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()


def days(per_day):
    """Une valeur par jour sur 90 jours : per_day(i) -> float."""
    return [(T0 + i * DAY, per_day(i)) for i in range(90)]


def fake(missing=()):
    def query(promql, start, end, step):
        table = {
            'nova_members{school="demo",state="active"}': [({}, days(lambda i: 20 + (i >= 31) + (i >= 59)))],
            'nova_active_users{school="demo",period="month"}': [({}, days(lambda i: 3 + i % 30))],
            "stage_seconds_count": [({"stage": "transcription"}, days(lambda i: 10)),
                                    ({"stage": "mise_en_forme"}, days(lambda i: 4))],
            'nova_ai_envelope_eur{school="demo",kind="annual"}': [({}, days(lambda i: 1000))],
            'nova_ai_envelope_eur{school="demo",kind="spent"}': [({}, days(lambda i: i))],
            'nova_ai_envelope_percent{school="demo"}': [({}, days(lambda i: 8.9))],
            "nova_uptime_seconds": [({}, [(T0 + h * 3600, 1.0) for h in range(0, 24 * 90, 2)])],
        }
        for key, result in table.items():
            if key in promql and not any(m in promql for m in missing):
                return result
        return []
    return query


def text_of(path):
    return " ".join(page.extract_text() for page in PdfReader(path).pages)


def test_totals_are_exact():
    data = annual_report.collect("demo", START, END, fake())
    assert data["members"] == {"2026-01": 20, "2026-02": 21, "2026-03": 22}
    assert data["active"]["2026-01"] == 32  # max sur le mois (jours 0 à 30 : 3 + 29)
    assert data["dictations"] == {"2026-01": 310, "2026-02": 280, "2026-03": 310}
    assert data["stages"] == {"transcription": 900, "mise_en_forme": 360}
    assert data["envelope"] == {"annual": 1000, "spent": 89}
    assert data["availability"] == pytest.approx(0.5, abs=0.01)


def test_pdf_is_produced_with_the_figures(tmp_path):
    out = tmp_path / "r.pdf"
    annual_report.render_pdf(annual_report.collect("demo", START, END, fake()), str(out))
    text = text_of(out)
    assert "demo" in text and "310" in text and "89.00" in text
    assert "non mesuré" in text  # les heures d'audio ne sont pas comptées


def test_missing_series_say_not_measured(tmp_path):
    out = tmp_path / "r.pdf"
    data = annual_report.collect("demo", START, END, fake(missing=("envelope", "uptime", "stage_seconds")))
    assert data["envelope"] is None and data["availability"] is None and data["dictations"] is None
    annual_report.render_pdf(data, str(out))
    assert text_of(out).count("non mesuré") >= 5


def test_small_group_is_never_counted(tmp_path):
    query = fake()
    small = lambda p, a, b, c: [({}, days(lambda i: 3))] if "nova_members" in p or "nova_active" in p else query(p, a, b, c)  # noqa: E731
    out = tmp_path / "r.pdf"
    annual_report.render_pdf(annual_report.collect("demo", START, END, small), str(out))
    text = text_of(out)
    assert "310" not in text and "moins de 5" in text


def test_no_email_can_appear(tmp_path):
    with pytest.raises(ValueError):
        annual_report.pdf_text("contact: eleve@ecole.fr")
    with pytest.raises(ValueError):
        annual_report.collect("a@b.fr", START, END, fake())
    out = tmp_path / "r.pdf"
    annual_report.render_pdf(annual_report.collect("demo", START, END, fake()), str(out))
    assert "@" not in text_of(out)
