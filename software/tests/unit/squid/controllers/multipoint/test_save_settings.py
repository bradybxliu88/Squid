"""Tests for the per-acquisition saving settings snapshot (SaveSettings).

The file saving format and the zarr options can be changed at runtime from Preferences, which updates _def. The
settings in effect when an acquisition starts are captured once (SaveSettings), pick the worker's saving job
classes, and travel with every job to the JobRunner subprocess, whose own _def copy can be older.

Ported from upstream aa9107d9 (tests/control/core/test_save_job_selection.py, test_MultiPointController.py).
"""

import dataclasses
import time

import numpy as np
import pytest
import tifffile

import _def
import squid.core.abc
from _def import FileSavingOption, ZarrChunkMode, ZarrCompression
from squid.backend.controllers.multipoint.job_processing import (
    CaptureInfo,
    DownsampledViewJob,
    JobImage,
    JobRunner,
    SaveImageJob,
    SaveOMETiffJob,
    SaveSettings,
    SaveZarrJob,
)
from squid.backend.controllers.multipoint.multi_point_worker import save_job_classes_for_format
from squid.core.config.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings


@pytest.fixture(autouse=True)
def _real_disk_io(monkeypatch):
    monkeypatch.setattr(_def, "SIMULATED_DISK_IO_ENABLED", False)


def _capture_info(save_directory, file_id="0_0_0") -> CaptureInfo:
    return CaptureInfo(
        position=squid.core.abc.Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            illumination_settings=IlluminationSettings(illumination_channel="BF LED matrix full", intensity=5.0),
            camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
        ),
        save_directory=str(save_directory),
        file_id=file_id,
        region_id=1,
        fov=0,
        configuration_idx=0,
        time_point=0,
    )


def _image() -> np.ndarray:
    return np.arange(32 * 32, dtype=np.uint16).reshape(32, 32)


def _stack_path(save_directory, info: CaptureInfo):
    return save_directory / f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}_stack.tiff"


@pytest.mark.parametrize(
    "file_saving_option, expected",
    [
        (FileSavingOption.INDIVIDUAL_IMAGES, [SaveImageJob]),
        (FileSavingOption.MULTI_PAGE_TIFF, [SaveImageJob]),
        (FileSavingOption.OME_TIFF, [SaveOMETiffJob]),
        (FileSavingOption.ZARR_V3, [SaveZarrJob]),
    ],
)
def test_save_job_classes_for_format(file_saving_option, expected):
    assert save_job_classes_for_format(file_saving_option) == expected


class TestSaveSettings:
    def test_reads_the_settings_in_effect_when_created(self, monkeypatch):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.ZARR_V3)
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 7)
        monkeypatch.setattr(_def, "ZARR_CHUNK_MODE", ZarrChunkMode.TILED_512)
        monkeypatch.setattr(_def, "ZARR_COMPRESSION", ZarrCompression.BEST)
        monkeypatch.setattr(_def, "ZARR_USE_6D_FOV_DIMENSION", True)

        settings = SaveSettings()

        assert settings.file_saving_option == FileSavingOption.ZARR_V3
        assert settings.tiff_compression_level == 7
        assert settings.zarr_chunk_mode == ZarrChunkMode.TILED_512
        assert settings.zarr_compression == ZarrCompression.BEST
        assert settings.zarr_use_6d_fov_dimension is True

    def test_is_a_snapshot(self, monkeypatch):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
        settings = SaveSettings()

        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.OME_TIFF)  # e.g. Preferences saved later

        assert settings.file_saving_option == FileSavingOption.INDIVIDUAL_IMAGES
        with pytest.raises(dataclasses.FrozenInstanceError):
            settings.file_saving_option = FileSavingOption.OME_TIFF  # type: ignore[misc]

    def test_every_job_type_carries_settings(self, tmp_path):
        settings = SaveSettings(file_saving_option=FileSavingOption.MULTI_PAGE_TIFF)
        info = _capture_info(tmp_path)
        for job_class in (SaveImageJob, SaveOMETiffJob, SaveZarrJob, DownsampledViewJob):
            job = job_class(capture_info=info, capture_image=JobImage(image_array=_image()), save_settings=settings)
            assert job.save_settings is settings


class TestSaveImageJobUsesTheFormatItCarries:
    def test_multi_page_tiff_although_def_says_individual_images(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
        info = _capture_info(tmp_path)
        job = SaveImageJob(
            capture_info=info,
            capture_image=JobImage(image_array=_image()),
            save_settings=SaveSettings(file_saving_option=FileSavingOption.MULTI_PAGE_TIFF),
        )

        assert job.run()

        assert _stack_path(tmp_path, info).exists()
        assert not list(tmp_path.glob("0_0_0_*.tiff"))

    def test_individual_images_although_def_says_multi_page_tiff(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.MULTI_PAGE_TIFF)
        info = _capture_info(tmp_path)
        job = SaveImageJob(
            capture_info=info,
            capture_image=JobImage(image_array=_image()),
            save_settings=SaveSettings(file_saving_option=FileSavingOption.INDIVIDUAL_IMAGES),
        )

        assert job.run()

        assert not _stack_path(tmp_path, info).exists()
        assert list(tmp_path.glob("0_0_0_*.tiff"))


def test_settings_changed_after_the_saving_subprocess_started_reach_it(tmp_path, monkeypatch):
    """The JobRunner subprocess keeps the _def it started with; the settings have to arrive with the job."""
    monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
    monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 0)
    runner = JobRunner()
    runner.daemon = True
    runner.start()
    try:
        assert runner.wait_ready(timeout_s=10)

        # What saving the Preferences dialog does, after the subprocess exists.
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.MULTI_PAGE_TIFF)
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 6)
        info = _capture_info(tmp_path)
        assert runner.dispatch(
            SaveImageJob(capture_info=info, capture_image=JobImage(image_array=_image()), save_settings=SaveSettings())
        )

        deadline = time.monotonic() + 20
        while runner.has_pending() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not runner.has_pending(), "The saving subprocess did not finish the job"
        result = runner.output_queue().get(timeout=5)
        assert result.exception is None
    finally:
        runner.shutdown(timeout_s=5)

    assert _stack_path(tmp_path, info).exists(), "Nothing was saved as MULTI_PAGE_TIFF"
    with tifffile.TiffFile(_stack_path(tmp_path, info)) as tif:
        assert tif.pages[0].compression == tifffile.COMPRESSION.ADOBE_DEFLATE
