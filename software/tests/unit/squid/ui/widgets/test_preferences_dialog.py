"""Tests for PreferencesDialog widget.

Ported from upstream: software/tests/control/test_preferences_dialog.py
"""
import os
import sys
import tempfile
from configparser import ConfigParser
from unittest.mock import patch, MagicMock

import pytest
from qtpy.QtWidgets import QMessageBox

import _def
from squid.ui.widgets.config import PreferencesDialog


@pytest.fixture
def sample_config():
    """Create a sample config with test values."""
    config = ConfigParser()
    config.add_section("GENERAL")
    config.set("GENERAL", "file_saving_option", "OME_TIFF")
    config.set("GENERAL", "default_saving_path", "/test/path")
    config.set("GENERAL", "multipoint_autofocus_channel", "BF LED matrix full")
    config.set("GENERAL", "enable_flexible_multipoint", "True")
    config.set("GENERAL", "max_velocity_x_mm", "30.0")
    config.set("GENERAL", "enable_tracking", "false")

    config.add_section("CAMERA_CONFIG")
    config.set("CAMERA_CONFIG", "binning_factor_default", "2")
    config.set("CAMERA_CONFIG", "flip_image", "None")
    config.set("CAMERA_CONFIG", "temperature_default", "20")
    config.set("CAMERA_CONFIG", "roi_width_default", "None")

    config.add_section("AF")
    config.set("AF", "stop_threshold", "0.85")
    config.set("AF", "crop_width", "800")

    config.add_section("SOFTWARE_POS_LIMIT")
    config.set("SOFTWARE_POS_LIMIT", "x_positive", "115.0")

    config.add_section("TRACKING")
    config.set("TRACKING", "default_tracker", "csrt")
    config.set("TRACKING", "search_area_ratio", "10")

    return config


@pytest.fixture
def temp_config_file(sample_config):
    """Create a temporary config file."""
    fd, filepath = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    with open(filepath, "w") as f:
        sample_config.write(f)
    yield filepath
    if os.path.exists(filepath):
        os.remove(filepath)


@pytest.fixture
def preferences_dialog(qtbot, sample_config, temp_config_file):
    """Create a PreferencesDialog instance for testing."""
    dialog = PreferencesDialog(sample_config, temp_config_file)
    qtbot.addWidget(dialog)
    return dialog


class TestConfigHelpers:
    """Test config value retrieval helper methods."""

    def test_get_config_value_existing(self, preferences_dialog):
        result = preferences_dialog._get_config_value("GENERAL", "file_saving_option", "default")
        assert result == "OME_TIFF"

    def test_get_config_value_missing_option(self, preferences_dialog):
        result = preferences_dialog._get_config_value("GENERAL", "nonexistent", "default_value")
        assert result == "default_value"

    def test_get_config_value_missing_section(self, preferences_dialog):
        result = preferences_dialog._get_config_value("NONEXISTENT", "option", "default_value")
        assert result == "default_value"

    def test_get_config_bool_true_lowercase(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "true")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_true_capitalized(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "True")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_one(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "1")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_yes(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "yes")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_false(self, preferences_dialog):
        result = preferences_dialog._get_config_bool("GENERAL", "enable_tracking", True)
        assert result is False

    def test_get_config_bool_missing(self, preferences_dialog):
        result = preferences_dialog._get_config_bool("GENERAL", "nonexistent", True)
        assert result is True

    def test_get_config_int_valid(self, preferences_dialog):
        result = preferences_dialog._get_config_int("CAMERA_CONFIG", "binning_factor_default", 1)
        assert result == 2

    def test_get_config_int_invalid(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "bad_int", "not_a_number")
        result = preferences_dialog._get_config_int("GENERAL", "bad_int", 99)
        assert result == 99

    def test_get_config_int_missing(self, preferences_dialog):
        result = preferences_dialog._get_config_int("GENERAL", "nonexistent", 42)
        assert result == 42

    def test_get_config_float_valid(self, preferences_dialog):
        result = preferences_dialog._get_config_float("AF", "stop_threshold", 0.5)
        assert result == 0.85

    def test_get_config_float_invalid(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "bad_float", "not_a_float")
        result = preferences_dialog._get_config_float("GENERAL", "bad_float", 1.5)
        assert result == 1.5

    def test_get_config_float_missing(self, preferences_dialog):
        result = preferences_dialog._get_config_float("GENERAL", "nonexistent", 3.14)
        assert result == 3.14


class TestIniValuesWithInlineComments:
    """The running software reads the ini with _def.conf_attribute_reader, which strips an inline comment.
    The dialog has to read the same text the same way: a value it misreads is shown as something the machine is not
    running, and is written back as that the next time ANY setting is saved.

    Ported from upstream 74824168 (tests/control/test_preferences_dialog.py)."""

    def _dialog(self, qtbot, config, path):
        dialog = PreferencesDialog(config, path)
        qtbot.addWidget(dialog)
        return dialog

    def test_the_helpers_read_a_commented_value_as_the_application_does(self, qtbot, sample_config, temp_config_file):
        sample_config.set("GENERAL", "max_velocity_x_mm", "30.0  # mm/s, this stage")
        sample_config.set("CAMERA_CONFIG", "binning_factor_default", "2\t# 2x2")
        sample_config.set("GENERAL", "enable_flexible_multipoint", "True  # needed for the plate workflow")
        sample_config.set("GENERAL", "enable_tracking", "false # off")
        sample_config.set("GENERAL", "file_saving_option", "OME_TIFF  # not zarr yet")
        sample_config.set("CAMERA_CONFIG", "flip_image", "None  # no flip")
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog._get_config_float("GENERAL", "max_velocity_x_mm", 1.0) == 30.0
        assert dialog._get_config_int("CAMERA_CONFIG", "binning_factor_default", 1) == 2
        assert dialog._get_config_bool("GENERAL", "enable_flexible_multipoint", False) is True
        assert dialog._get_config_bool("GENERAL", "enable_tracking", True) is False
        assert dialog._get_config_value("GENERAL", "file_saving_option", "") == "OME_TIFF"
        assert dialog._get_config_value("CAMERA_CONFIG", "flip_image", "") == "None"
        for section, option in (("GENERAL", "max_velocity_x_mm"), ("CAMERA_CONFIG", "binning_factor_default")):
            raw = sample_config.get(section, option)
            assert dialog._get_config_float(section, option, -1.0) == float(_def.conf_attribute_reader(raw))

    def test_text_that_only_looks_like_a_comment_is_kept(self, qtbot, sample_config, temp_config_file):
        sample_config.set("GENERAL", "default_saving_path", "/data/run#3")
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog._get_config_value("GENERAL", "default_saving_path", "") == "/data/run#3"

    def test_the_rows_show_the_commented_values_and_list_no_change(self, qtbot, sample_config, temp_config_file):
        sample_config.set("CAMERA_CONFIG", "binning_factor_default", "2  # 2x2")
        sample_config.set("GENERAL", "enable_flexible_multipoint", "True  # plate workflow")
        sample_config.set("GENERAL", "file_saving_option", "OME_TIFF  # not zarr yet")
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog.binning_spinbox.value() == 2
        assert dialog.flexible_multipoint_checkbox.isChecked()
        assert dialog.file_saving_combo.currentText() == "OME_TIFF"
        assert not dialog._get_changes()

    def test_saving_does_not_reset_a_commented_value_to_the_default(self, qtbot, sample_config, temp_config_file):
        # binning 4 with a comment: the old reader saw "4  # ..." as not-an-int, showed the default 2, and wrote 2
        sample_config.set("CAMERA_CONFIG", "binning_factor_default", "4  # this camera")
        sample_config.set("GENERAL", "enable_flexible_multipoint", "False  # not on this machine")
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        dialog._apply_settings()
        saved = ConfigParser()
        saved.read(temp_config_file)
        assert _def.conf_attribute_reader(saved.get("CAMERA_CONFIG", "binning_factor_default")) == 4
        assert _def.conf_attribute_reader(saved.get("GENERAL", "enable_flexible_multipoint")) is False

    @pytest.mark.parametrize(
        "ini_text, meaning",
        [
            ('"C:/data/run #3"', "C:/data/run #3"),  # bare, it would become C:/data/run after any save
            (r'"C:\\data\\run #3"', r"C:\data\run #3"),  # backslashes survive the JSON quoting
            ("/data/run#3", "/data/run#3"),  # no whitespace before the #: never a comment, never quoted
            ("/test/path", "/test/path"),
            ('"2024"', "2024"),  # a folder named like a number stays text
        ],
    )
    def test_free_text_means_the_same_after_a_save(self, qtbot, sample_config, temp_config_file, ini_text, meaning):
        sample_config.set("GENERAL", "default_saving_path", ini_text)
        assert _def.conf_attribute_reader(ini_text) == meaning  # what the application runs with
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog.saving_path_edit.text() == meaning
        dialog.binning_spinbox.setValue(3)  # the operator changes an UNRELATED setting
        assert not [c for c in dialog._get_changes() if "Saving Path" in c[0]]
        dialog._apply_settings()
        saved = ConfigParser()
        saved.read(temp_config_file)
        assert _def.conf_attribute_reader(saved.get("GENERAL", "default_saving_path")) == meaning

    def test_autofocus_channel_means_the_same_after_a_save(self, qtbot, sample_config, temp_config_file):
        sample_config.set("GENERAL", "multipoint_autofocus_channel", '"BF #2"')
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog.autofocus_channel_edit.text() == "BF #2"
        dialog._apply_settings()
        saved = ConfigParser()
        saved.read(temp_config_file)
        assert _def.conf_attribute_reader(saved.get("GENERAL", "multipoint_autofocus_channel")) == "BF #2"

    def test_ordinary_text_is_written_unchanged(self, qtbot, sample_config, temp_config_file):
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog._ini_text("/test/path") == "/test/path"
        assert dialog._ini_text("BF LED matrix full") == "BF LED matrix full"
        assert dialog._ini_text("/data/run#3") == "/data/run#3"
        assert dialog._ini_text("C:/data/run #3") == '"C:/data/run #3"'
        assert dialog._ini_text("") == ""

    def test_a_missing_key_and_an_unreadable_value_still_give_the_default(self, qtbot, sample_config, temp_config_file):
        sample_config.set("CAMERA_CONFIG", "binning_factor_default", "two")
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog._get_config_int("CAMERA_CONFIG", "binning_factor_default", 3) == 3
        assert dialog._get_config_int("CAMERA_CONFIG", "no_such_key", 3) == 3
        assert dialog._get_config_float("NO_SECTION", "x", 1.5) == 1.5
        assert dialog._get_config_bool("GENERAL", "no_such_key", True) is True
        assert dialog._get_config_int("CAMERA_CONFIG", "temperature_default", 0) == 20

    def test_an_integral_float_is_accepted_for_an_integer_row(self, qtbot, sample_config, temp_config_file):
        """The application reads "5.0" as 5.0 and uses it; the dialog used to fall back to the default."""
        sample_config.set("CAMERA_CONFIG", "binning_factor_default", "4.0")
        dialog = self._dialog(qtbot, sample_config, temp_config_file)
        assert dialog._get_config_int("CAMERA_CONFIG", "binning_factor_default", 1) == 4
        sample_config.set("CAMERA_CONFIG", "binning_factor_default", "4.5")
        assert dialog._get_config_int("CAMERA_CONFIG", "binning_factor_default", 1) == 1


class TestFloatsEqual:
    """Test floating-point comparison helper."""

    def test_equal_values(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.0) is True

    def test_nearly_equal_within_epsilon(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.00005) is True

    def test_different_values(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.001) is False

    def test_custom_epsilon(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.001, epsilon=0.01) is True

    def test_negative_values(self, preferences_dialog):
        assert preferences_dialog._floats_equal(-1.0, -1.00005) is True

    def test_zero(self, preferences_dialog):
        assert preferences_dialog._floats_equal(0.0, 0.00005) is True


class TestEnsureSection:
    """Test section creation helper."""

    def test_ensure_existing_section(self, preferences_dialog):
        preferences_dialog._ensure_section("GENERAL")
        assert preferences_dialog.config.has_section("GENERAL")

    def test_ensure_new_section(self, preferences_dialog):
        assert not preferences_dialog.config.has_section("NEW_SECTION")
        preferences_dialog._ensure_section("NEW_SECTION")
        assert preferences_dialog.config.has_section("NEW_SECTION")


class TestChangeDetection:
    """Test change detection functionality."""

    def test_no_changes(self, preferences_dialog):
        changes = preferences_dialog._get_changes()
        assert len(changes) == 0

    def test_detect_string_change(self, preferences_dialog):
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "File Saving Format" for c in changes)

    def test_detect_bool_change(self, preferences_dialog):
        current = preferences_dialog.flexible_multipoint_checkbox.isChecked()
        preferences_dialog.flexible_multipoint_checkbox.setChecked(not current)
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "Enable Flexible Multipoint" for c in changes)

    def test_detect_int_change(self, preferences_dialog):
        preferences_dialog.binning_spinbox.setValue(4)
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "Default Binning Factor" for c in changes)

    def test_detect_float_change(self, preferences_dialog):
        preferences_dialog.max_vel_x.setValue(50.0)
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "Max Velocity X" for c in changes)

    def test_change_includes_restart_flag(self, preferences_dialog):
        preferences_dialog.binning_spinbox.setValue(4)
        changes = preferences_dialog._get_changes()
        binning_change = next(c for c in changes if c[0] == "Default Binning Factor")
        assert binning_change[3] is True  # requires_restart

    def test_live_setting_no_restart(self, preferences_dialog):
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        changes = preferences_dialog._get_changes()
        file_change = next(c for c in changes if c[0] == "File Saving Format")
        assert file_change[3] is False  # does not require restart


class TestApplySettings:
    """Test settings application."""

    def test_apply_settings_saves_to_file(self, preferences_dialog, temp_config_file):
        preferences_dialog.file_saving_combo.setCurrentText("INDIVIDUAL_IMAGES")
        preferences_dialog._apply_settings()

        # Read back the file
        saved_config = ConfigParser()
        saved_config.read(temp_config_file)
        assert saved_config.get("GENERAL", "file_saving_option") == "INDIVIDUAL_IMAGES"

    def test_apply_settings_creates_missing_sections(self, qtbot, temp_config_file):
        # Create config without TRACKING section
        config = ConfigParser()
        config.add_section("GENERAL")
        config.set("GENERAL", "file_saving_option", "OME_TIFF")
        with open(temp_config_file, "w") as f:
            config.write(f)

        dialog = PreferencesDialog(config, temp_config_file)
        qtbot.addWidget(dialog)
        dialog._apply_settings()

        # Verify TRACKING section was created
        saved_config = ConfigParser()
        saved_config.read(temp_config_file)
        assert saved_config.has_section("TRACKING")

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod doesn't reliably prevent writes on Windows")
    def test_apply_settings_handles_read_only_file(self, preferences_dialog, temp_config_file):
        os.chmod(temp_config_file, 0o444)
        try:
            # Should not raise, but show error dialog
            preferences_dialog._apply_settings()
        finally:
            os.chmod(temp_config_file, 0o644)

    def test_apply_settings_emits_signal(self, qtbot, preferences_dialog):
        """Verify signal_config_changed is emitted when settings are saved."""
        with qtbot.waitSignal(preferences_dialog.signal_config_changed, timeout=1000):
            preferences_dialog._apply_settings()


class TestSaveAndCloseWorkflow:
    """Test the complete save workflow via _save_and_close method."""

    def test_save_and_close_no_changes(self, qtbot, preferences_dialog):
        """When no changes, should close silently without dialog."""
        preferences_dialog.accept = MagicMock()
        preferences_dialog._save_and_close()
        preferences_dialog.accept.assert_called_once()

    def test_save_and_close_single_change_no_restart(self, qtbot, preferences_dialog, temp_config_file):
        """Single change that doesn't require restart should save silently."""
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")

        # Mock accept to track if dialog closes
        preferences_dialog.accept = MagicMock()
        preferences_dialog._save_and_close()

        # Should save and close without showing restart message
        preferences_dialog.accept.assert_called_once()

        # Verify saved
        saved_config = ConfigParser()
        saved_config.read(temp_config_file)
        assert saved_config.get("GENERAL", "file_saving_option") == "MULTI_PAGE_TIFF"

    def test_save_and_close_single_change_requires_restart(self, qtbot, preferences_dialog, temp_config_file):
        """Single change requiring restart should offer a restart."""
        preferences_dialog.binning_spinbox.setValue(4)

        # The restart prompt is modal: answer it "No" so the test neither blocks nor restarts.
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No) as mock_question:
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

            # Should offer a restart
            mock_question.assert_called_once()
            assert "restart" in str(mock_question.call_args)
            preferences_dialog.accept.assert_called_once()

    def test_save_and_close_multiple_changes_accepted(self, qtbot, preferences_dialog, temp_config_file):
        """Multiple changes should show confirmation dialog."""
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        preferences_dialog.binning_spinbox.setValue(4)

        # Mock the confirmation dialog to return Accepted, and decline the (modal) restart prompt
        with (
            patch("qtpy.QtWidgets.QDialog.exec_", return_value=True),
            patch.object(QMessageBox, "question", return_value=QMessageBox.No),
        ):
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

            # Should save and close
            preferences_dialog.accept.assert_called_once()

            # Verify both changes saved
            saved_config = ConfigParser()
            saved_config.read(temp_config_file)
            assert saved_config.get("GENERAL", "file_saving_option") == "MULTI_PAGE_TIFF"
            assert saved_config.get("CAMERA_CONFIG", "binning_factor_default") == "4"

    def test_save_and_close_multiple_changes_cancelled(self, qtbot, preferences_dialog, temp_config_file):
        """When user cancels confirmation dialog, settings should not be saved."""
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        preferences_dialog.binning_spinbox.setValue(4)

        # Mock the confirmation dialog to return Rejected (cancelled)
        with patch("qtpy.QtWidgets.QDialog.exec_", return_value=False):
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

            # Should NOT save or close
            preferences_dialog.accept.assert_not_called()

            # Verify original values preserved in file
            saved_config = ConfigParser()
            saved_config.read(temp_config_file)
            assert saved_config.get("GENERAL", "file_saving_option") == "OME_TIFF"
            assert saved_config.get("CAMERA_CONFIG", "binning_factor_default") == "2"


class TestUIInitialization:
    """Test UI is properly initialized from config."""

    def test_file_saving_combo_initialized(self, preferences_dialog):
        assert preferences_dialog.file_saving_combo.currentText() == "OME_TIFF"

    def test_saving_path_initialized(self, preferences_dialog):
        assert preferences_dialog.saving_path_edit.text() == "/test/path"

    def test_binning_spinbox_initialized(self, preferences_dialog):
        assert preferences_dialog.binning_spinbox.value() == 2

    def test_temperature_spinbox_initialized(self, preferences_dialog):
        assert preferences_dialog.temperature_spinbox.value() == 20

    def test_max_velocity_initialized(self, preferences_dialog):
        assert preferences_dialog.max_vel_x.value() == 30.0

    def test_af_threshold_initialized(self, preferences_dialog):
        assert preferences_dialog.af_stop_threshold.value() == 0.85


class TestFileSavingOptionFallback:
    """The file saving format shown must be the one in effect, even with no key in the config.

    Most machine configs don't set file_saving_option, so the fallback has to come from _def. With a hardcoded
    OME_TIFF fallback the dialog showed a format that wasn't in use, and since _apply_settings() always writes
    file_saving_option (and applies it live), saving any unrelated preference switched the machine to OME-TIFF.

    Ported from upstream aa9107d9 (tests/control/test_preferences_dialog.py).
    """

    @pytest.fixture
    def config_without_file_saving_option(self, sample_config):
        sample_config.remove_option("GENERAL", "file_saving_option")
        return sample_config

    def test_combo_falls_back_to_value_in_effect(
        self, qtbot, monkeypatch, config_without_file_saving_option, temp_config_file
    ):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", _def.FileSavingOption.MULTI_PAGE_TIFF)

        dialog = PreferencesDialog(config_without_file_saving_option, temp_config_file)
        qtbot.addWidget(dialog)

        assert dialog.file_saving_combo.currentText() == "MULTI_PAGE_TIFF"
        assert not dialog._get_changes(), "Untouched dialog must not report a format change"

    def test_saving_unrelated_setting_does_not_rewrite_format(
        self, qtbot, monkeypatch, config_without_file_saving_option, temp_config_file
    ):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", _def.FileSavingOption.INDIVIDUAL_IMAGES)

        dialog = PreferencesDialog(config_without_file_saving_option, temp_config_file)
        qtbot.addWidget(dialog)
        dialog.saving_path_edit.setText("/some/other/path")
        dialog._apply_settings()

        saved = ConfigParser()
        saved.read(temp_config_file)
        assert saved.get("GENERAL", "file_saving_option") == "INDIVIDUAL_IMAGES"
        # ... and the format applied live is still the one that was running.
        assert _def.FILE_SAVING_OPTION == _def.FileSavingOption.INDIVIDUAL_IMAGES

    def test_format_change_away_from_value_in_effect_is_listed(
        self, qtbot, monkeypatch, config_without_file_saving_option, temp_config_file
    ):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", _def.FileSavingOption.INDIVIDUAL_IMAGES)

        dialog = PreferencesDialog(config_without_file_saving_option, temp_config_file)
        qtbot.addWidget(dialog)
        dialog.file_saving_combo.setCurrentText("OME_TIFF")

        changes = {c[0]: c for c in dialog._get_changes()}
        assert changes["File Saving Format"][1:3] == ("INDIVIDUAL_IMAGES", "OME_TIFF")

    def test_config_value_still_wins_over_value_in_effect(self, qtbot, monkeypatch, sample_config, temp_config_file):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", _def.FileSavingOption.ZARR_V3)

        # sample_config sets file_saving_option = OME_TIFF, which must win over the fallback.
        dialog = PreferencesDialog(sample_config, temp_config_file)
        qtbot.addWidget(dialog)

        assert dialog.file_saving_combo.currentText() == "OME_TIFF"


class TestTiffCompressionSetting:
    """Tests for the TIFF Compression Level control on the General tab (ported from upstream aa9107d9)."""

    @pytest.fixture(autouse=True)
    def _running_level(self, monkeypatch):
        # _apply_settings() pushes the level into _def; keep that from leaking into other tests.
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 0)
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", _def.FILE_SAVING_OPTION)

    def test_defaults_to_off(self, preferences_dialog):
        assert preferences_dialog.tiff_compression_spinbox.value() == 0

    def test_initialized_from_config(self, qtbot, sample_config, temp_config_file):
        sample_config.set("GENERAL", "tiff_compression_level", "6")

        dialog = PreferencesDialog(sample_config, temp_config_file)
        qtbot.addWidget(dialog)

        assert dialog.tiff_compression_spinbox.value() == 6

    def test_initialized_from_a_commented_config_value(self, qtbot, sample_config, temp_config_file):
        sample_config.set("GENERAL", "tiff_compression_level", "6  # smaller files")

        dialog = PreferencesDialog(sample_config, temp_config_file)
        qtbot.addWidget(dialog)

        assert dialog.tiff_compression_spinbox.value() == 6
        assert not [c for c in dialog._get_changes() if c[0] == "TIFF Compression Level"]

    def test_falls_back_to_the_level_in_effect(self, qtbot, monkeypatch, sample_config, temp_config_file):
        """With no key in the config, show the running value rather than assuming a default."""
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 3)

        dialog = PreferencesDialog(sample_config, temp_config_file)
        qtbot.addWidget(dialog)

        assert dialog.tiff_compression_spinbox.value() == 3
        assert not [c for c in dialog._get_changes() if c[0] == "TIFF Compression Level"]

    def test_out_of_range_config_value_is_not_a_change(self, qtbot, sample_config, temp_config_file):
        """The spin box shows the nearest level it has, which is also the one in effect."""
        sample_config.set("GENERAL", "tiff_compression_level", "12")

        dialog = PreferencesDialog(sample_config, temp_config_file)
        qtbot.addWidget(dialog)

        assert dialog.tiff_compression_spinbox.value() == 9
        assert not [c for c in dialog._get_changes() if c[0] == "TIFF Compression Level"]

    def test_only_shown_for_the_formats_that_support_it(self, preferences_dialog):
        for file_saving_option, visible in (
            ("INDIVIDUAL_IMAGES", True),
            ("MULTI_PAGE_TIFF", True),
            ("OME_TIFF", False),  # written through a memmap, which can't be compressed
            ("ZARR_V3", False),
        ):
            preferences_dialog.file_saving_combo.setCurrentText(file_saving_option)
            assert preferences_dialog.tiff_compression_spinbox.isVisibleTo(preferences_dialog) is visible
            assert preferences_dialog.tiff_compression_label.isVisibleTo(preferences_dialog) is visible
            # The zarr rows keep their own rule.
            assert preferences_dialog.zarr_compression_combo.isVisibleTo(preferences_dialog) is (
                file_saving_option == "ZARR_V3"
            )

    def test_change_is_detected_and_needs_no_restart(self, preferences_dialog):
        preferences_dialog.tiff_compression_spinbox.setValue(6)

        changes = {c[0]: c for c in preferences_dialog._get_changes()}
        assert changes["TIFF Compression Level"][1:] == ("0", "6", False)

    def test_saved_on_its_own(self, qtbot, preferences_dialog, temp_config_file):
        """A setting missing from _get_changes() is silently dropped when it is the only change."""
        preferences_dialog.tiff_compression_spinbox.setValue(9)

        with patch("qtpy.QtWidgets.QDialog.exec_", return_value=True):
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

        saved = ConfigParser()
        saved.read(temp_config_file)
        assert saved.getint("GENERAL", "tiff_compression_level") == 9

    def test_save_pushes_level_to_def_module(self, qtbot, preferences_dialog):
        preferences_dialog.tiff_compression_spinbox.setValue(4)

        with patch("qtpy.QtWidgets.QDialog.exec_", return_value=True):
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

        assert _def.TIFF_COMPRESSION_LEVEL == 4
