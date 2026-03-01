"""Tests for weather_rss_gui.py — uses unittest.mock to avoid GUI/systemd/filesystem deps."""

import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub tkinter before importing the module so no display is required
# ---------------------------------------------------------------------------

class _FakeTk:
    def __init__(self): pass
    def title(self, s): pass
    def geometry(self, s): pass
    def after(self, ms, fn): pass
    def bell(self): pass
    def mainloop(self): pass

_tk = types.ModuleType("tkinter")
_tk.Tk = _FakeTk
_tk.StringVar = MagicMock
_tk.END = "end"
_tk.Text = MagicMock

_ttk = types.ModuleType("tkinter.ttk")
_ttk.Label = MagicMock
_ttk.Frame = MagicMock
_ttk.Button = MagicMock
_ttk.Treeview = MagicMock

_msgbox = types.ModuleType("tkinter.messagebox")
_msgbox.showwarning = MagicMock()

sys.modules["tkinter"] = _tk
sys.modules["tkinter.ttk"] = _ttk
sys.modules["tkinter.messagebox"] = _msgbox

import weather_rss_gui as gui  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: build a WeatherRSSMonitor without touching the display
# ---------------------------------------------------------------------------

def _make_monitor():
    """Instantiate WeatherRSSMonitor with UI/refresh suppressed, then attach mock widgets."""
    with patch.object(gui.WeatherRSSMonitor, "create_ui"), \
         patch.object(gui.WeatherRSSMonitor, "refresh_all"):
        mon = gui.WeatherRSSMonitor()

    mon.status_var = MagicMock()
    mon.uptime_var = MagicMock()
    mon.status_label = MagicMock()
    mon.log_box = MagicMock()
    mon.tree = MagicMock()
    mon.tree.get_children.return_value = []
    mon.after = MagicMock()
    mon.bell = MagicMock()
    return mon


# ---------------------------------------------------------------------------
# run() helper
# ---------------------------------------------------------------------------

class TestRun(unittest.TestCase):
    @patch("weather_rss_gui.subprocess.check_output", return_value="active\n")
    def test_returns_stdout_on_success(self, mock_co):
        result = gui.run(["systemctl", "is-active", "svc"])
        self.assertEqual(result, "active\n")
        mock_co.assert_called_once()

    @patch("weather_rss_gui.subprocess.check_output",
           side_effect=__import__("subprocess").CalledProcessError(1, "cmd", output="err msg"))
    def test_returns_output_on_failure(self, _):
        result = gui.run(["bad", "cmd"])
        self.assertEqual(result, "err msg")


# ---------------------------------------------------------------------------
# refresh_service_status()
# ---------------------------------------------------------------------------

class TestRefreshServiceStatus(unittest.TestCase):
    def setUp(self):
        self.mon = _make_monitor()

    def _call(self, status_str):
        with patch("weather_rss_gui.run", return_value=status_str):
            with patch.object(self.mon, "alert") as mock_alert:
                with patch.object(self.mon, "detect_restart_loop"):
                    self.mon.refresh_service_status()
        return mock_alert

    def test_active_sets_uppercase_status(self):
        self._call("active")
        self.mon.status_var.set.assert_called_with("ACTIVE")

    def test_active_sets_green(self):
        self._call("active")
        self.mon.status_label.config.assert_called_with(foreground="green")

    def test_inactive_sets_red(self):
        self._call("inactive")
        self.mon.status_label.config.assert_called_with(foreground="red")

    def test_activating_sets_orange(self):
        self._call("activating")
        self.mon.status_label.config.assert_called_with(foreground="orange")

    def test_failed_triggers_alert(self):
        mock_alert = self._call("failed")
        mock_alert.assert_called_once_with("Service FAILED")

    def test_non_failed_no_alert(self):
        mock_alert = self._call("active")
        mock_alert.assert_not_called()


# ---------------------------------------------------------------------------
# refresh_uptime()
# ---------------------------------------------------------------------------

class TestRefreshUptime(unittest.TestCase):
    def setUp(self):
        self.mon = _make_monitor()

    def test_parses_timestamp(self):
        with patch("weather_rss_gui.run", return_value="ActiveEnterTimestamp=Mon 2026-03-01 12:00:00 UTC\n"):
            self.mon.refresh_uptime()
        self.mon.uptime_var.set.assert_called_with("Mon 2026-03-01 12:00:00 UTC")

    def test_empty_timestamp_shows_na(self):
        with patch("weather_rss_gui.run", return_value="ActiveEnterTimestamp=\n"):
            self.mon.refresh_uptime()
        self.mon.uptime_var.set.assert_called_with("N/A")

    def test_no_equals_sign_is_ignored(self):
        with patch("weather_rss_gui.run", return_value="garbage output"):
            self.mon.refresh_uptime()
        self.mon.uptime_var.set.assert_not_called()


# ---------------------------------------------------------------------------
# detect_restart_loop()
# ---------------------------------------------------------------------------

class TestDetectRestartLoop(unittest.TestCase):
    def setUp(self):
        self.mon = _make_monitor()

    def _call(self, log_output):
        with patch("weather_rss_gui.run", return_value=log_output):
            with patch.object(self.mon, "alert") as mock_alert:
                self.mon.detect_restart_loop()
        return mock_alert

    def test_below_threshold_no_alert(self):
        logs = "Starting\n" * (gui.RESTART_THRESHOLD - 1)
        mock_alert = self._call(logs)
        mock_alert.assert_not_called()

    def test_at_threshold_triggers_alert(self):
        logs = "Starting\n" * gui.RESTART_THRESHOLD
        mock_alert = self._call(logs)
        mock_alert.assert_called_once()
        self.assertIn("Restart loop", mock_alert.call_args[0][0])

    def test_above_threshold_includes_count(self):
        count = gui.RESTART_THRESHOLD + 3
        logs = "Starting\n" * count
        mock_alert = self._call(logs)
        self.assertIn(str(count), mock_alert.call_args[0][0])


# ---------------------------------------------------------------------------
# refresh_feed_health()
# ---------------------------------------------------------------------------

class TestRefreshFeedHealth(unittest.TestCase):
    def setUp(self):
        self.mon = _make_monitor()

    def _fake_xml(self, name, age_seconds, size_bytes):
        p = MagicMock(spec=Path)
        p.name = name
        stat = MagicMock()
        stat.st_mtime = time.time() - age_seconds
        stat.st_size = size_bytes
        p.stat.return_value = stat
        return p

    def _inserted_values(self):
        return self.mon.tree.insert.call_args[1]["values"]

    def _patch_feeds(self, xml_files):
        mock_dir = MagicMock()
        mock_dir.glob.return_value = xml_files
        return patch("weather_rss_gui.FEEDS_DIR", mock_dir)

    def test_ok_feed(self):
        xml = self._fake_xml("weather.xml", age_seconds=60, size_bytes=2048)
        with self._patch_feeds([xml]):
            self.mon.refresh_feed_health()
        self.assertEqual(self._inserted_values()[0], "weather.xml")
        self.assertEqual(self._inserted_values()[4], "OK")

    def test_stale_feed(self):
        xml = self._fake_xml("old.xml", age_seconds=31 * 60, size_bytes=1024)
        with self._patch_feeds([xml]):
            self.mon.refresh_feed_health()
        self.assertEqual(self._inserted_values()[4], "STALE")

    def test_empty_feed(self):
        xml = self._fake_xml("empty.xml", age_seconds=60, size_bytes=0)
        with self._patch_feeds([xml]):
            self.mon.refresh_feed_health()
        self.assertEqual(self._inserted_values()[4], "EMPTY")

    def test_clears_existing_rows_first(self):
        self.mon.tree.get_children.return_value = ["row1", "row2"]
        with self._patch_feeds([]):
            self.mon.refresh_feed_health()
        self.mon.tree.delete.assert_any_call("row1")
        self.mon.tree.delete.assert_any_call("row2")

    def test_no_feeds_no_insert(self):
        with self._patch_feeds([]):
            self.mon.refresh_feed_health()
        self.mon.tree.insert.assert_not_called()


# ---------------------------------------------------------------------------
# Service control: start / stop / restart
# ---------------------------------------------------------------------------

class TestServiceControl(unittest.TestCase):
    def setUp(self):
        self.mon = _make_monitor()

    def test_start_calls_systemctl_start(self):
        with patch("weather_rss_gui.run") as mock_run, \
             patch.object(self.mon, "refresh_all"):
            self.mon.start()
        mock_run.assert_called_once_with(["systemctl", "start", gui.SERVICE])

    def test_stop_calls_systemctl_stop(self):
        with patch("weather_rss_gui.run") as mock_run, \
             patch.object(self.mon, "refresh_all"):
            self.mon.stop()
        mock_run.assert_called_once_with(["systemctl", "stop", gui.SERVICE])

    def test_restart_calls_systemctl_restart(self):
        with patch("weather_rss_gui.run") as mock_run, \
             patch.object(self.mon, "refresh_all"):
            self.mon.restart()
        mock_run.assert_called_once_with(["systemctl", "restart", gui.SERVICE])

    def test_each_action_calls_refresh_all(self):
        for method in ("start", "stop", "restart"):
            with patch("weather_rss_gui.run"), \
                 patch.object(self.mon, "refresh_all") as mock_refresh:
                getattr(self.mon, method)()
            mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# refresh_logs()
# ---------------------------------------------------------------------------

class TestRefreshLogs(unittest.TestCase):
    def test_clears_then_inserts_log_output(self):
        mon = _make_monitor()
        with patch("weather_rss_gui.run", return_value="log line 1\nlog line 2\n"):
            mon.refresh_logs()
        mon.log_box.delete.assert_called_once_with("1.0", "end")
        mon.log_box.insert.assert_called_once_with("end", "log line 1\nlog line 2\n")


# ---------------------------------------------------------------------------
# alert()
# ---------------------------------------------------------------------------

class TestAlert(unittest.TestCase):
    def test_rings_bell_and_shows_warning(self):
        mon = _make_monitor()
        _msgbox.showwarning.reset_mock()
        mon.alert("Test alert")
        mon.bell.assert_called_once()
        _msgbox.showwarning.assert_called_once_with("Weather RSS Alert", "Test alert")


if __name__ == "__main__":
    unittest.main()
