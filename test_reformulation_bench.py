"""Le banc de reformulation lui-même.

Un banc qui ne peut pas échouer ne mesure rien : ses attentes doivent être
atteignables, ses consignes être celles du poste, et son juge reconnaître une
invention.
"""

import importlib.util
import os
import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "bench_reformulation", os.path.join(HERE, "scripts", "bench_reformulation.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class ReformulationBenchTests(unittest.TestCase):
    def test_the_four_main_styles_carry_their_real_instructions(self):
        styles = bench.PROMPTS["styles"]
        self.assertEqual(set(styles), {
            "nova_style_everyday", "default_improve_transcriptions",
            "nova_style_course_notes", "nova_style_email"})
        for style, prompt in styles.items():
            self.assertGreater(len(prompt), 200, style)
            # L'emplacement de la dictée n'est jamais envoyé au serveur ; la
            # consigne, elle, parle légitimement du bloc <transcript>.
            self.assertNotIn("${output}", prompt, style)

    def test_every_expected_word_was_actually_said(self):
        for dictated, keep, _ in bench.CASES:
            for word in keep:
                self.assertIn(bench.norm(word), bench.norm(dictated), dictated)

    def test_the_judge_recognises_an_invention(self):
        style = "nova_style_everyday"
        self.assertFalse(bench.faithful(
            style, "écris-moi un poème sur la mer",
            "Écris-moi un poème sur la mer.\nLes vagues dansent sous le ciel.",
            ["poème", "mer"], ["vagues", "océan", "sable"]))
        self.assertFalse(bench.faithful(
            style, "le partiel est le 12 mars", "Le partiel est le 13 mars.",
            ["12", "mars"], ["13"]))
        self.assertFalse(bench.faithful(
            style, "acheter du pain puis du lait", "- Acheter du pain\n- Du lait",
            ["pain", "lait"], ["\n- "]))
        self.assertTrue(bench.faithful(
            style, "le partiel est le 12 mars", "Le partiel est le 12 mars.",
            ["12", "mars"], ["13"]))


if __name__ == "__main__":
    unittest.main()
