"""serve.py installe le journal structuré : sans lui, les événements sortent nus."""

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ.setdefault("NOVA_DB", os.path.join(TEST_DIRECTORY.name, "serve.db"))
os.environ.setdefault("NOVA_DOMAINS", "example.com")

import observability  # noqa: E402
import serve  # noqa: E402


class ServeLoggingTests(unittest.TestCase):
    def test_run_installs_the_json_journal_on_stdout(self):
        self.addCleanup(observability.configure_logging, "INFO")
        out = io.StringIO()
        unavailable = {"available": False, "error_code": "x", "reason": ""}
        with mock.patch.object(serve.local_channel, "transport_plan", return_value=unavailable), \
                contextlib.redirect_stdout(out):
            self.assertEqual(asyncio.run(serve.run()), 1)
            observability.log("ai.usage", task="voice_command", prompt_tokens=120)
        lines = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
        self.assertEqual(lines[-1]["event"], "ai.usage")
        self.assertEqual(lines[-1]["prompt_tokens"], 120)


if __name__ == "__main__":
    unittest.main()
