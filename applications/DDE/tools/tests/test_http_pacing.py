"""Regression tests for core/http.py pacing functions (#59).

Covers cross-invocation disk pacing, backward clock-jump capping,
corrupted pace file recovery, OSError fallback to in-memory pacing,
and in-memory pacing behaviour.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test so
# that the import succeeds even when ``requests`` or ``click`` are not
# installed in the test environment.
with patch.dict("sys.modules", {"requests": MagicMock(), "click": MagicMock()}):
    from dde.core import http
    from dde.core.http import _pace, _pace_disk, _pace_memory


class TestPaceDisk(unittest.TestCase):
    """Tests for ``_pace_disk`` — the flock-based cross-invocation pacer."""

    def setUp(self):
        self._orig_pace_dir = http._PACE_DIR
        self._tmpdir = tempfile.mkdtemp(prefix="dde_pace_test_")
        http._PACE_DIR = Path(self._tmpdir)

    def tearDown(self):
        http._PACE_DIR = self._orig_pace_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. Basic interval enforcement
    # ------------------------------------------------------------------
    def test_pace_disk_enforces_interval(self):
        """When a pace file records a recent timestamp, ``_pace_disk``
        should sleep for the remainder of the interval."""
        host = "api.example.com"
        interval = 1.0  # 1 QPS

        # First call: no pace file yet → previous=0.0, now=1000.0
        # wait = 1.0 - (1000.0 - 0.0) < 0 → no sleep
        # Second call: pace file contains ~1000.0, now=1000.3
        # wait = 1.0 - (1000.3 - 1000.0) = 0.7 → sleep(0.7)
        time_values = iter([1000.0, 1000.0, 1000.3, 1000.3])

        with patch("time.time", side_effect=lambda: next(time_values)), \
             patch("time.sleep") as mock_sleep:
            _pace_disk(host, interval)
            mock_sleep.assert_not_called()

            _pace_disk(host, interval)
            mock_sleep.assert_called_once()
            slept = mock_sleep.call_args[0][0]
            self.assertAlmostEqual(slept, 0.7, places=5)

    # ------------------------------------------------------------------
    # 2. Backward clock jump is capped
    # ------------------------------------------------------------------
    def test_pace_disk_caps_backward_clock_jump(self):
        """If the wall clock jumps backward, the sleep must be capped at
        ``interval``, not ``interval + |jump|``."""
        host = "api.example.com"
        interval = 1.0

        # First call writes timestamp 1000.0.
        # Second call reads 1000.0 but now=500.0 (clock jumped back 500s).
        # Uncapped wait = 1.0 - (500.0 - 1000.0) = 501.0
        # Capped wait  = min(501.0, 1.0) = 1.0
        time_values = iter([1000.0, 1000.0, 500.0, 500.0])

        with patch("time.time", side_effect=lambda: next(time_values)), \
             patch("time.sleep") as mock_sleep:
            _pace_disk(host, interval)

            _pace_disk(host, interval)
            mock_sleep.assert_called_once()
            slept = mock_sleep.call_args[0][0]
            self.assertAlmostEqual(slept, interval, places=5)

    # ------------------------------------------------------------------
    # 3. Corrupted pace file falls back to previous=0.0
    # ------------------------------------------------------------------
    def test_pace_disk_corrupted_file(self):
        """A pace file with non-numeric content should be treated as 0.0
        rather than raising ``ValueError``."""
        host = "api.example.com"
        interval = 1.0

        # Pre-create a corrupted pace file.
        pace_file = http._PACE_DIR / host
        pace_file.parent.mkdir(parents=True, exist_ok=True)
        pace_file.write_text("NOT_A_NUMBER\n")

        # now=2000.0, previous falls back to 0.0
        # wait = 1.0 - (2000.0 - 0.0) < 0 → no sleep
        with patch("time.time", return_value=2000.0), \
             patch("time.sleep") as mock_sleep:
            _pace_disk(host, interval)  # Should not raise
            mock_sleep.assert_not_called()


class TestPaceFallback(unittest.TestCase):
    """Tests for ``_pace`` dispatch and ``_pace_memory`` fallback."""

    def setUp(self):
        self._orig_pace_dir = http._PACE_DIR
        self._orig_last_call = http._last_call.copy()
        http._last_call.clear()

    def tearDown(self):
        http._PACE_DIR = self._orig_pace_dir
        http._last_call.clear()
        http._last_call.update(self._orig_last_call)

    # ------------------------------------------------------------------
    # 4. OSError in _pace_disk triggers _pace_memory fallback
    # ------------------------------------------------------------------
    def test_pace_fallback_on_oserror(self):
        """When ``_pace_disk`` raises ``OSError``, ``_pace`` should fall
        back to ``_pace_memory``."""
        # Point _PACE_DIR at a non-existent, non-creatable path.
        http._PACE_DIR = Path("/nonexistent/root/dde_pace_test")

        url = "https://api.example.com/resource"

        with patch("time.sleep"), \
             patch("time.monotonic", return_value=9999.0):
            _pace(url, qps=1.0)

        # _pace_memory should have recorded the host.
        self.assertIn("api.example.com", http._last_call)

    # ------------------------------------------------------------------
    # 5. In-memory pacing preserves original behaviour
    # ------------------------------------------------------------------
    def test_pace_memory_preserves_original_behavior(self):
        """``_pace_memory`` should use ``time.monotonic`` and enforce
        the interval between calls to the same host."""
        host = "api.example.com"
        interval = 1.0

        mono_values = iter([100.0, 100.0, 100.4, 100.4])

        with patch("time.monotonic", side_effect=lambda: next(mono_values)), \
             patch("time.sleep") as mock_sleep:
            # First call — no previous entry, no sleep.
            _pace_memory(host, interval)
            mock_sleep.assert_not_called()

            # Second call — 0.4s elapsed, should sleep 0.6s.
            _pace_memory(host, interval)
            mock_sleep.assert_called_once()
            slept = mock_sleep.call_args[0][0]
            self.assertAlmostEqual(slept, 0.6, places=5)

    # ------------------------------------------------------------------
    # 6. qps=0 disables pacing entirely
    # ------------------------------------------------------------------
    def test_pace_zero_qps_skips(self):
        """``_pace`` with qps<=0 should return immediately."""
        with patch.object(http, "_pace_disk") as mock_disk, \
             patch.object(http, "_pace_memory") as mock_mem:
            _pace("https://example.com", qps=0)
            mock_disk.assert_not_called()
            mock_mem.assert_not_called()


if __name__ == "__main__":
    unittest.main()
