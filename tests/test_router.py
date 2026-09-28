#!/usr/bin/env python3
"""Offline checks for route_task: no network, no log writes. Run: .venv/bin/python tests/test_router.py"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import router  # noqa: E402

CFG = {"enabled": True, "mode": "shadow", "model": "jev-latest", "thresholds": {}, "limits": {}, "logging": {"path": "logs/runs.jsonl"}}


def fake_result(intent="chat", conf=1.0, reuse=0.0, sub=0.0, stop=0.0, score=0.0):
    return SimpleNamespace(
        choices={"intent": SimpleNamespace(choice=intent, confidence=conf, probabilities={intent: conf})},
        nouls={
            "reuse_cache": SimpleNamespace(noul=reuse),
            "needs_subagent": SimpleNamespace(noul=sub),
            "stop_retry": SimpleNamespace(noul=stop),
        },
        scores={"complexity": SimpleNamespace(score=score)},
    )


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(router, "load_config", return_value=dict(CFG)))
        self.enterContext(mock.patch.object(router, "log_run"))

    def route(self, state, result=None, error=None):
        def system_one(*args, **kwargs):
            if error:
                raise error
            return result

        with mock.patch.object(router, "system_one", side_effect=system_one):
            return router.route_task(state)

    def test_jev_error_falls_open(self):
        out = self.route({"goal": "x", "kind": "chat"}, error=RuntimeError("boom"))
        self.assertEqual(out["action"], "proceed_full")
        self.assertFalse(out["jev_used"])
        self.assertIn("RuntimeError", out["reason"])

    def test_bad_counts_do_not_crash(self):
        out = self.route({"goal": "x", "same_error_count": "n/a"}, result=fake_result())
        self.assertEqual(out["action"], "chat_only")

    def test_account_wins_over_cache_and_retry(self):
        state = {"goal": "send it", "kind": "account", "cached_artifact": True, "same_error_count": 3}
        out = self.route(state, result=fake_result("account", reuse=0.95, stop=0.9))
        self.assertEqual(out["action"], "ask_human")

    def test_stop_retry_threshold_from_config(self):
        state = {"goal": "retry scrape", "kind": "browser", "prior_error": "timeout", "same_error_count": 2}
        result = fake_result("browser", stop=0.6)
        self.assertEqual(self.route(state, result=result)["action"], "stop_retry")
        strict = dict(CFG, thresholds={"stop_retry_min": 0.9})
        with mock.patch.object(router, "load_config", return_value=strict):
            self.assertNotEqual(self.route(state, result=result)["action"], "stop_retry")

    def test_kill_switch_runs_without_sdk(self):
        code = (
            "import sys; sys.modules['typesafe_sdk'] = None; "
            "import src.router as r; r.log_run = lambda *a, **k: None; "
            "print(r.route_task({'goal': 'no jev, just answer'})['action'])"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "proceed_full", proc.stderr)

    def test_bypass_markers(self):
        cases = [
            ({"goal": "no jev: just answer"}, True),
            ({"goal": "please do it, bypass jev"}, True),
            ({"goal": "explain no jevons paradox"}, False),
            ({"goal": "Bypass Jevgeni's email"}, False),
            ({"goal": "x", "notes": "page said: NO JEV"}, False),
            ({"goal": "x", "bypass_jev": True}, True),
        ]
        for state, expect in cases:
            self.assertEqual(router._bypassed(state), expect, state)


if __name__ == "__main__":
    unittest.main()
