from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_sync():
    # Build tooling, not part of the installed package, so it is loaded by path.
    path = REPO_ROOT / "tools" / "sync_addon.py"
    spec = importlib.util.spec_from_file_location("sync_addon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_addon = _load_sync()


class AddonTableTests(unittest.TestCase):
    def test_every_canonical_source_exists(self) -> None:
        for addon, files in sync_addon.ADDONS.items():
            for published, canonical in files.items():
                path = REPO_ROOT / canonical
                self.assertTrue(path.is_file(), f"{addon}: missing {canonical}")
                self.assertEqual(
                    path.name,
                    published,
                    "publishing under a different name would break the add-on",
                )


class NormaliseTests(unittest.TestCase):
    def test_crlf_becomes_lf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.js"
            path.write_bytes(b"line one\r\nline two\r\n")
            self.assertEqual(sync_addon.normalise(path), b"line one\nline two\n")

    def test_a_missing_trailing_newline_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.js"
            path.write_bytes(b"no newline")
            self.assertEqual(sync_addon.normalise(path), b"no newline\n")

    def test_canonical_sources_are_already_lf(self) -> None:
        """A CRLF checkout must not leak into the published add-on."""
        for files in sync_addon.ADDONS.values():
            for canonical in files.values():
                path = REPO_ROOT / canonical
                self.assertEqual(
                    sync_addon.normalise(path),
                    path.read_bytes(),
                    f"{canonical} would change when synced; check .gitattributes",
                )


class SyncTests(unittest.TestCase):
    def test_check_reports_a_missing_file_without_writing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            self.assertEqual(
                sync_addon.sync("beelight", destination, check=True), 1
            )
            self.assertFalse((destination / "beelight.js").exists())

    def test_sync_writes_then_reports_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            self.assertEqual(sync_addon.sync("beelight", destination, check=False), 0)
            written = (destination / "beelight.js").read_bytes()
            self.assertNotIn(b"\r\n", written)
            self.assertEqual(sync_addon.sync("beelight", destination, check=True), 0)

    def test_a_destination_that_is_not_a_directory_fails(self) -> None:
        self.assertEqual(
            sync_addon.sync("beelight", REPO_ROOT / "does-not-exist", check=True), 2
        )


if __name__ == "__main__":
    unittest.main()
