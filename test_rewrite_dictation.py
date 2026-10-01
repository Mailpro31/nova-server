"""Le circuit de reformulation, sans la route HTTP.

`/api/reformulate` mêlait l'authentification, le quota, les journaux et le
circuit lui-même : reprises, Style, nettoyage, contrôles. Le banc de mesure
du serveur doit rejouer exactement ce que reçoit l'élève, sans compte ni
jeton : le circuit est donc une fonction, `rewrite_dictation`, appelée par la
route comme par le banc.
"""

import asyncio
import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import TaskType

main.init_db()


def run(dictated, answer, style_id="nova_style_everyday", resolution=None):
    async def style_engine(task_type, instruction, message):
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def resolve_engine(task_type, instruction, text, examples):
        return resolution if resolution is not None else dictated

    with patch.object(main, "execute_text", style_engine), \
            patch.object(main, "execute_text_with_examples", resolve_engine):
        return asyncio.run(main.rewrite_dictation(
            dictated, "Mets au propre.", TaskType.STYLE_TRANSFORM, style_id))


class RewriteDictationTests(unittest.TestCase):
    def test_a_good_rewrite_comes_back_with_ok(self):
        outcome = run("on se voit mercredi à 14 heures", "On se voit mercredi à 14 heures.")
        self.assertEqual(outcome.reason, "ok")
        self.assertEqual(outcome.text, "On se voit mercredi à 14 heures.")

    def test_a_refused_rewrite_carries_its_reason_and_no_text(self):
        outcome = run("le partiel est le 12 mars", "Le partiel est le 13 mars.")
        self.assertIsNone(outcome.text)
        # Le 12 a disparu : le contrôle des chiffres explicites le voit en premier.
        self.assertEqual(outcome.reason, "explicit-number-lost")

    def test_a_truncated_rewrite_is_reported(self):
        outcome = run("une longue dictée", main.RewriteTruncated())
        self.assertIsNone(outcome.text)
        self.assertEqual(outcome.reason, "truncated")

    def test_the_style_receives_the_resolved_dictation(self):
        outcome = run("on se voit mardi non attends mercredi",
                      "On se voit mercredi.", resolution="on se voit mercredi")
        self.assertEqual(outcome.reason, "ok")
        self.assertEqual(outcome.resolved, "on se voit mercredi")


if __name__ == "__main__":
    unittest.main()
