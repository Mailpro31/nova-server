"""« Analyser un document » lit un PDF, un Word ou un PowerPoint (n° 8 quinquies).

Capture de Sash, 21/10 : seul un texte collé était lu, et les termes partaient
dans le dictionnaire sans relecture (n° 69). L'aperçu rend les termes sans les
ajouter ; l'élève coche ceux qu'il garde.
"""

import io
import unittest
import zipfile

from test_main import AdminTestCase

import main

main.init_db()


def office(path_in_zip: str, xml: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(path_in_zip, xml)
    return buffer.getvalue()


def pdf(text: str) -> bytes:
    """Un PDF d'une page, écrit à la main : une police standard et une ligne."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return out


class DocumentTextTests(unittest.TestCase):
    def test_a_word_document_is_read(self):
        docx = office("word/document.xml",
                      "<w:document><w:p><w:r><w:t>Le nombre de Reynolds</w:t></w:r>"
                      "<w:r><w:t> selon Navier-Stokes</w:t></w:r></w:p></w:document>")
        self.assertIn("Navier-Stokes", main.document_text("cours.docx", docx))

    def test_a_powerpoint_is_read(self):
        pptx = office("ppt/slides/slide1.xml", "<p:sld><a:t>Profil NACA</a:t></p:sld>")
        self.assertIn("NACA", main.document_text("cours.pptx", pptx))

    def test_a_pdf_is_read(self):
        self.assertIn("ONERA", main.document_text("cours.pdf", pdf("Essais ONERA")))

    def test_a_broken_pdf_is_unreadable_not_a_crash(self):
        """Contrôle réel du PC fixe (25/10) : un PDF abîmé donnait une erreur 500."""
        with self.assertRaises(ValueError):
            main.document_text("cours.pdf", b"%PDF-1.4 pas un pdf")

    def test_plain_text_is_read(self):
        self.assertEqual(main.document_text("notes.txt", "Bernoulli".encode()), "Bernoulli")


class TermCandidatesTests(unittest.TestCase):
    def test_proper_nouns_acronyms_and_compound_names_are_found(self):
        terms = main.term_candidates(
            "Le nombre de Reynolds caractérise l'écoulement. "
            "Les essais ONERA suivent Navier-Stokes.")
        for term in ("Reynolds", "ONERA", "Navier-Stokes"):
            with self.subTest(term=term):
                self.assertIn(term, terms)

    def test_a_word_capitalised_by_its_sentence_is_left_out(self):
        self.assertNotIn("Les", main.term_candidates("Les essais commencent. Les mesures suivent."))


class PreviewRouteTests(AdminTestCase):
    def test_a_preview_returns_terms_and_adds_none(self):
        token = self.sign_in("eleve@example.com")
        docx = office("word/document.xml", "<w:t>Le profil NACA et la loi de Bernoulli</w:t>")
        response = self.client.post(
            "/api/dictionary/analyze?preview=1", headers=self.auth(token),
            files={"file": ("cours.docx", docx, "application/octet-stream")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("NACA", response.json()["terms"])
        with main.db() as conn:
            stored = conn.execute(
                "SELECT COUNT(*) FROM personal_dictionary WHERE email=?",
                ("eleve@example.com",)).fetchone()[0]
        self.assertEqual(stored, 0)


if __name__ == "__main__":
    unittest.main()
