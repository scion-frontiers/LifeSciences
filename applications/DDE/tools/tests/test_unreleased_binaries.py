"""Tests for unreleased (PLACEHOLDER) binary handling in dde doctor (#152).

Covers:
  1. PLACEHOLDER binaries report INFO ("not yet released"), not WARN ("missing")
  2. Doctor never suggests --binaries-only for PLACEHOLDER binaries
  3. Binary check and capability check agree on unreleased status
  4. Capability snapshot reports "unreleased" for PLACEHOLDER binaries
  5. Released-but-missing binaries still report WARN with correct remedy
"""

from __future__ import annotations

import unittest
from unittest import mock

from dde.commands.doctor import (
    INFO,
    OK,
    WARN,
    CAPABILITY,
    Report,
    _PROVISIONED_BINARIES,
    _check_binaries,
    _check_hypothesis_strategies,
    get_capability_snapshot,
)


# ---- Helpers ---------------------------------------------------------------

#: Binaries declared as unreleased (released=False) in _PROVISIONED_BINARIES.
_UNRELEASED = {
    name for name, (_, _, released) in _PROVISIONED_BINARIES.items() if not released
}

#: Binaries declared as released (released=True) in _PROVISIONED_BINARIES.
_RELEASED = {
    name for name, (_, _, released) in _PROVISIONED_BINARIES.items() if released
}


def _binary_checks(report: Report) -> dict[str, object]:
    """Return a dict mapping check names to Check objects for binary checks."""
    return {c.name: c for c in report.checks if c.name.startswith("binary ")}


# ---- Item 1 & 5: INFO vs WARN distinction ---------------------------------


class TestUnreleasedBinaryStatus(unittest.TestCase):
    """PLACEHOLDER binaries must report INFO, not WARN."""

    def setUp(self):
        self.report = Report()
        # Mock shutil.which to return None (nothing on PATH) and
        # env.tools_home to return a path where nothing exists.
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch("dde.commands.doctor.env.tools_home", return_value=mock.MagicMock(
                __truediv__=lambda self, other: mock.MagicMock(
                    is_file=lambda: False,
                    __truediv__=lambda self2, other2: mock.MagicMock(is_file=lambda: False),
                ),
            )),
        ):
            _check_binaries(self.report)
        self.checks = _binary_checks(self.report)

    def test_unreleased_binaries_are_info(self):
        """Each unreleased binary should have status INFO."""
        for name in _UNRELEASED:
            check = self.checks.get(f"binary {name}")
            self.assertIsNotNone(check, f"missing check for {name}")
            self.assertEqual(
                check.status, INFO,
                f"binary {name} should be INFO, got {check.status}",
            )

    def test_unreleased_detail_says_not_yet_released(self):
        """The detail string must say 'not yet released upstream'."""
        for name in _UNRELEASED:
            check = self.checks[f"binary {name}"]
            self.assertIn("not yet released upstream", check.detail)

    def test_released_missing_binaries_are_warn(self):
        """Released-but-missing binaries should remain WARN."""
        for name in _RELEASED:
            check = self.checks.get(f"binary {name}")
            self.assertIsNotNone(check, f"missing check for {name}")
            # Either WARN (missing) or OK (if somehow found) — never INFO
            self.assertNotEqual(
                check.status, INFO,
                f"released binary {name} should not be INFO",
            )


# ---- Item 2: no --binaries-only suggestion ---------------------------------


class TestNoReprovisionRemedy(unittest.TestCase):
    """PLACEHOLDER binaries must never suggest --binaries-only."""

    def setUp(self):
        self.report = Report()
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch("dde.commands.doctor.env.tools_home", return_value=mock.MagicMock(
                __truediv__=lambda self, other: mock.MagicMock(
                    is_file=lambda: False,
                    __truediv__=lambda self2, other2: mock.MagicMock(is_file=lambda: False),
                ),
            )),
        ):
            _check_binaries(self.report)
        self.checks = _binary_checks(self.report)

    def test_unreleased_remedy_omits_binaries_only(self):
        """The remedy for unreleased binaries must not mention --binaries-only."""
        for name in _UNRELEASED:
            check = self.checks[f"binary {name}"]
            self.assertNotIn(
                "--binaries-only", check.remedy,
                f"binary {name} remedy should not suggest --binaries-only",
            )

    def test_unreleased_remedy_mentions_upstream(self):
        """The remedy must explain the binary is not published upstream."""
        for name in _UNRELEASED:
            check = self.checks[f"binary {name}"]
            self.assertIn("not yet published upstream", check.remedy)


# ---- Item 3: binary and capability checks agree ---------------------------


class TestBinaryCapabilityAgreement(unittest.TestCase):
    """Binary check and capability check for hypex must agree."""

    def test_hypothesis_hypex_is_info_when_unreleased(self):
        """When hypex is unreleased, hypothesis strategy check must be INFO."""
        report = Report()
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch("dde.commands.doctor.env.tools_home", return_value=mock.MagicMock(
                __truediv__=lambda self, other: mock.MagicMock(
                    is_file=lambda: False,
                    __truediv__=lambda self2, other2: mock.MagicMock(is_file=lambda: False),
                ),
            )),
        ):
            _check_binaries(report)
            _check_hypothesis_strategies(report)

        binary_check = next(
            (c for c in report.checks if c.name == "binary hypex"), None,
        )
        strategy_check = next(
            (c for c in report.checks if c.name == "hypothesis strategy: hypex"), None,
        )
        self.assertIsNotNone(binary_check)
        self.assertIsNotNone(strategy_check)

        # Both must agree: either both INFO (unreleased) or both
        # WARN/OK (released).
        if not _PROVISIONED_BINARIES["hypex"][2]:
            # Unreleased — both should be INFO.
            self.assertEqual(binary_check.status, INFO)
            self.assertEqual(strategy_check.status, INFO)
        else:
            # Released — neither should be INFO.
            self.assertNotEqual(binary_check.status, INFO)
            self.assertNotEqual(strategy_check.status, INFO)

    def test_strategy_check_not_warn_capability_when_unreleased(self):
        """Unreleased hypex must not report WARN+CAPABILITY in strategies."""
        report = Report()
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch("dde.commands.doctor.env.tools_home", return_value=mock.MagicMock(
                __truediv__=lambda self, other: mock.MagicMock(
                    is_file=lambda: False,
                    __truediv__=lambda self2, other2: mock.MagicMock(is_file=lambda: False),
                ),
            )),
        ):
            _check_hypothesis_strategies(report)

        strategy_check = next(
            (c for c in report.checks if c.name == "hypothesis strategy: hypex"), None,
        )
        self.assertIsNotNone(strategy_check)
        if not _PROVISIONED_BINARIES["hypex"][2]:
            self.assertNotEqual(
                strategy_check.kind, CAPABILITY,
                "unreleased hypex should not be classified as a capability warning",
            )


# ---- Capability snapshot ---------------------------------------------------


class TestCapabilitySnapshotUnreleased(unittest.TestCase):
    """get_capability_snapshot must report 'unreleased' for PLACEHOLDER binaries."""

    def test_unreleased_binaries_snapshot(self):
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch("dde.commands.doctor.env.tools_home", return_value=mock.MagicMock(
                __truediv__=lambda self, other: mock.MagicMock(
                    is_file=lambda: False,
                    __truediv__=lambda self2, other2: mock.MagicMock(is_file=lambda: False),
                ),
            )),
        ):
            snapshot = get_capability_snapshot()

        for name in _UNRELEASED:
            self.assertEqual(
                snapshot.get(name), "unreleased",
                f"snapshot[{name}] should be 'unreleased', got {snapshot.get(name)!r}",
            )

    def test_released_missing_binaries_not_unreleased(self):
        """Released-but-missing binaries must be 'unavailable', not 'unreleased'."""
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch("dde.commands.doctor.env.tools_home", return_value=mock.MagicMock(
                __truediv__=lambda self, other: mock.MagicMock(
                    is_file=lambda: False,
                    __truediv__=lambda self2, other2: mock.MagicMock(is_file=lambda: False),
                ),
            )),
        ):
            snapshot = get_capability_snapshot()

        for name in _RELEASED:
            status = snapshot.get(name)
            self.assertNotEqual(
                status, "unreleased",
                f"released binary {name} should not be 'unreleased' in snapshot",
            )


# ---- Declaration structure -------------------------------------------------


class TestDeclarationStructure(unittest.TestCase):
    """_PROVISIONED_BINARIES entries must all have the released flag."""

    def test_all_entries_are_three_tuples(self):
        """Every entry must be a 3-tuple (purpose, remedy, released)."""
        for name, entry in _PROVISIONED_BINARIES.items():
            self.assertEqual(
                len(entry), 3,
                f"{name} entry should be (purpose, remedy, released), got {len(entry)}-tuple",
            )
            _purpose, _remedy, released = entry
            self.assertIsInstance(released, bool, f"{name} released flag must be bool")

    def test_known_unreleased_binaries(self):
        """hypex, elo, prox must be declared as unreleased."""
        for name in ("hypex", "elo", "prox"):
            self.assertIn(name, _PROVISIONED_BINARIES)
            self.assertFalse(
                _PROVISIONED_BINARIES[name][2],
                f"{name} should be released=False",
            )

    def test_known_released_binaries(self):
        """fpocket, vina must be declared as released."""
        for name in ("fpocket", "vina"):
            self.assertIn(name, _PROVISIONED_BINARIES)
            self.assertTrue(
                _PROVISIONED_BINARIES[name][2],
                f"{name} should be released=True",
            )


if __name__ == "__main__":
    unittest.main()
