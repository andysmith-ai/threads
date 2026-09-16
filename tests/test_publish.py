from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "publish"))
import publish as publisher  # noqa: E402


class SidecarStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_cwd = os.getcwd()
        self._temporary = tempfile.TemporaryDirectory()
        os.chdir(self._temporary.name)

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        self._temporary.cleanup()

    def write_state(self, slug: str, record: dict) -> None:
        Path("state").mkdir(exist_ok=True)
        Path("state", f"{slug}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_load_state_reads_sidecars(self) -> None:
        first = {"status": "published", "last_media_id": "1"}
        second = {"status": "sending", "actor": "agent"}
        self.write_state("first", first)
        self.write_state("second", second)
        Path("state.json").write_text("not valid JSON", encoding="utf-8")

        self.assertEqual(publisher._load_state(), {"first": first, "second": second})

    def test_save_writes_and_stages_only_requested_sidecar(self) -> None:
        record = {"status": "sending", "actor": "andy"}
        with mock.patch.object(publisher, "_commit_push") as commit_push:
            publisher._save("one", record, "claim one", push=True)

        self.assertEqual(json.loads(Path("state/one.json").read_text()), record)
        self.assertFalse(Path("state.json").exists())
        commit_push.assert_called_once_with(os.path.join("state", "one.json"), "claim one")

    def test_parent_resolution_loads_parent_sidecar(self) -> None:
        self.write_state(
            "parent",
            {"status": "published", "last_media_id": "media-1"},
        )
        child = publisher.Post("child", "agent", "parent", None, None, ("reply",))
        self.assertEqual(publisher._parent_id(child), "media-1")

        self.write_state("parent", {"status": "sending", "last_media_id": "media-1"})
        self.assertIsNone(publisher._parent_id(child))

    def test_sending_sidecar_prevents_automatic_republish(self) -> None:
        Path("posts").mkdir()
        Path("posts/already-claimed.md").write_text(
            "---\nactor: andy\n---\nclaimed post\n",
            encoding="utf-8",
        )
        self.write_state("already-claimed", {"status": "sending", "actor": "andy"})

        with (
            mock.patch.object(sys, "argv", ["publish.py", "--no-push"]),
            mock.patch.object(publisher, "publish_text") as publish_text,
        ):
            self.assertEqual(publisher.main(), 0)
        publish_text.assert_not_called()

    def test_push_rebases_and_retries_after_concurrent_change(self) -> None:
        completed = lambda args, code=0, stderr="": subprocess.CompletedProcess(
            args, code, stdout="", stderr=stderr
        )
        results = [
            completed(["git", "add"]),
            completed(["git", "commit"]),
            completed(["git", "push"], 1, "rejected"),
            completed(["git", "fetch"]),
            completed(["git", "rebase"]),
            completed(["git", "diff"], 1),
            completed(["git", "push"]),
        ]
        with mock.patch.object(publisher.subprocess, "run", side_effect=results) as run:
            publisher._commit_push("state/one.json", "record one")

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["git", "add", "--", "state/one.json"],
                [
                    "git",
                    "commit",
                    "-m",
                    "record one",
                    "--only",
                    "--",
                    "state/one.json",
                ],
                ["git", "push"],
                ["git", "fetch", "origin"],
                ["git", "rebase", "@{upstream}"],
                [
                    "git",
                    "diff",
                    "--quiet",
                    "@{upstream}...HEAD",
                    "--",
                    "state/one.json",
                ],
                ["git", "push"],
            ],
        )

    def test_rebase_conflict_is_a_hard_failure(self) -> None:
        completed = lambda args, code=0, stderr="": subprocess.CompletedProcess(
            args, code, stdout="", stderr=stderr
        )
        results = [
            completed(["git", "add"]),
            completed(["git", "commit"]),
            completed(["git", "push"], 1, "rejected"),
            completed(["git", "fetch"]),
            completed(["git", "rebase"], 1, "conflict"),
            completed(["git", "rebase", "--abort"]),
        ]
        with mock.patch.object(publisher.subprocess, "run", side_effect=results) as run:
            with self.assertRaisesRegex(publisher.StatePushError, "manual resolution"):
                publisher._commit_push("state/one.json", "record one")

        self.assertEqual(run.call_args_list[-1].args[0], ["git", "rebase", "--abort"])

    def test_identical_concurrent_claim_does_not_publish_twice(self) -> None:
        completed = lambda args, code=0, stderr="": subprocess.CompletedProcess(
            args, code, stdout="", stderr=stderr
        )
        results = [
            completed(["git", "add"]),
            completed(["git", "commit"]),
            completed(["git", "push"], 1, "rejected"),
            completed(["git", "fetch"]),
            completed(["git", "rebase"]),
            completed(["git", "diff"]),
        ]
        with mock.patch.object(publisher.subprocess, "run", side_effect=results) as run:
            with self.assertRaisesRegex(
                publisher.StatePushError, "concurrent publication"
            ):
                publisher._commit_push("state/one.json", "claim one")

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands.count(["git", "push"]), 1)


if __name__ == "__main__":
    unittest.main()
