"""Tests for optional lossless zlib compression of saved TIFFs (_def.TIFF_COMPRESSION_LEVEL).

Ported from upstream aa9107d9 (tests/control/test_tiff_compression.py and tests/control/test_def.py).
"""

import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import imageio
import numpy as np
import pytest
import tifffile

import _def
import squid.core.abc
from _def import FileSavingOption, tiff_compression_level_from_config
from squid.backend.controllers.multipoint.acquisition_planner import AcquisitionPlanner
from squid.backend.controllers.multipoint.job_processing import CaptureInfo, JobImage, SaveImageJob, SaveSettings
from squid.backend.io import utils_acquisition
from squid.core.config.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings


@pytest.fixture(autouse=True)
def _plain_saving(monkeypatch):
    monkeypatch.setattr(_def, "SIMULATED_DISK_IO_ENABLED", False)
    monkeypatch.setattr(_def, "SAVE_IN_PSEUDO_COLOR", False)


@pytest.fixture
def channel():
    return AcquisitionChannel(
        name="BF LED matrix full",
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(illumination_channel="BF LED matrix full", intensity=5.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
        z_offset_um=0.0,
    )


@pytest.fixture
def compressible_image():
    """A frame with the smooth structure of a real image, so compression can do something."""
    y, x = np.mgrid[0:256, 0:256]
    return ((np.sin(x / 16) * np.cos(y / 24) + 1) * 8000).astype(np.uint16)


def capture_info(save_directory, channel, file_id="0_0_0"):
    return CaptureInfo(
        position=squid.core.abc.Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=channel,
        save_directory=str(save_directory),
        file_id=file_id,
        region_id=1,
        fov=0,
        configuration_idx=0,
        z_piezo_um=None,
        time_point=0,
    )


def save_individual_image(image, save_directory, channel, level, file_id="0_0_0"):
    utils_acquisition.save_image(
        image=image,
        file_id=file_id,
        save_directory=str(save_directory),
        config=channel,
        is_color=False,
        tiff_compression_level=level,
    )
    return utils_acquisition.get_image_filepath(str(save_directory), file_id, channel.name, image.dtype)


def save_stack_page(image, save_directory, channel, level):
    info = capture_info(save_directory, channel)
    job = SaveImageJob(
        capture_info=info,
        capture_image=JobImage(image_array=image),
        save_settings=SaveSettings(file_saving_option=FileSavingOption.MULTI_PAGE_TIFF, tiff_compression_level=level),
    )
    assert job.run()
    return save_directory / f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}_stack.tiff"


class TestTiffCompressionLevelFromConfig:
    @pytest.mark.parametrize("value, expected", [(0, 0), (6, 6), (6.5, 6), (12, 9), (-1, 0)])
    def test_limited_to_the_zlib_levels(self, value, expected):
        assert tiff_compression_level_from_config(value) == expected

    @pytest.mark.parametrize("value", ["fast", "", None])
    def test_not_a_number_means_uncompressed(self, value):
        """A bad value in the machine config must not keep the software from starting."""
        assert tiff_compression_level_from_config(value) == 0

    def test_a_commented_ini_value_is_read(self):
        """The loader strips the comment first, as for every [GENERAL] value."""
        assert tiff_compression_level_from_config(_def.conf_attribute_reader("6  # smaller files")) == 6

    def test_default_is_uncompressed(self):
        import inspect

        assert "TIFF_COMPRESSION_LEVEL = 0" in inspect.getsource(_def)


class TestCompressionKwargs:
    def test_level_zero_writes_no_compression_arguments(self):
        assert utils_acquisition.tiff_compression_kwargs(0) == {}

    def test_level_selects_zlib_with_the_predictor(self):
        assert utils_acquisition.tiff_compression_kwargs(6) == {
            "compression": "zlib",
            "compressionargs": {"level": 6},
            "predictor": True,
        }

    @pytest.mark.parametrize(
        "file_saving_option, expected",
        [
            (FileSavingOption.INDIVIDUAL_IMAGES, 6),
            (FileSavingOption.MULTI_PAGE_TIFF, 6),
            (FileSavingOption.OME_TIFF, 0),  # memory-mapped, can't be compressed
            (FileSavingOption.ZARR_V3, 0),  # has its own codecs
        ],
    )
    def test_level_only_applies_to_the_tiff_formats_that_support_it(self, file_saving_option, expected):
        assert utils_acquisition.tiff_compression_level_for_format(file_saving_option, 6) == expected


class TestIndividualImages:
    def test_uncompressed_by_default(self, tmp_path, channel, compressible_image):
        path = save_individual_image(compressible_image, tmp_path, channel, level=0)

        with tifffile.TiffFile(path) as tif:
            assert tif.pages[0].compression == tifffile.COMPRESSION.NONE

    def test_compressed_and_lossless(self, tmp_path, channel, compressible_image):
        path = save_individual_image(compressible_image, tmp_path, channel, level=6)

        with tifffile.TiffFile(path) as tif:
            page = tif.pages[0]
            assert page.compression == tifffile.COMPRESSION.ADOBE_DEFLATE
            assert page.predictor == tifffile.PREDICTOR.HORIZONTAL
        np.testing.assert_array_equal(tifffile.imread(path), compressible_image)

    def test_smaller_than_uncompressed(self, tmp_path, channel, compressible_image):
        off_dir = tmp_path / "off"
        on_dir = tmp_path / "on"
        off_dir.mkdir()
        on_dir.mkdir()

        uncompressed = save_individual_image(compressible_image, off_dir, channel, level=0)
        compressed = save_individual_image(compressible_image, on_dir, channel, level=6)

        assert os.path.getsize(compressed) < os.path.getsize(uncompressed)

    def test_non_tiff_format_is_unaffected(self, tmp_path, channel, monkeypatch):
        """8 bit images go to Acquisition.IMAGE_FORMAT, whose non-TIFF plugins reject these arguments."""
        monkeypatch.setattr(_def.Acquisition, "IMAGE_FORMAT", "bmp")
        image = np.full((32, 32), 42, dtype=np.uint8)

        path = save_individual_image(image, tmp_path, channel, level=6)

        assert path.endswith(".bmp")
        np.testing.assert_array_equal(imageio.imread(path), image)

    def test_saving_job_writes_the_level_it_carries(self, tmp_path, channel, compressible_image, monkeypatch):
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 0)  # what the saving subprocess's _def may still say
        info = capture_info(tmp_path, channel)
        job = SaveImageJob(
            capture_info=info,
            capture_image=JobImage(image_array=compressible_image),
            save_settings=SaveSettings(file_saving_option=FileSavingOption.INDIVIDUAL_IMAGES, tiff_compression_level=6),
        )

        assert job.run()

        path = utils_acquisition.get_image_filepath(str(tmp_path), info.file_id, channel.name, np.uint16)
        with tifffile.TiffFile(path) as tif:
            assert tif.pages[0].compression == tifffile.COMPRESSION.ADOBE_DEFLATE


class TestMultiPageTiff:
    def test_uncompressed_by_default(self, tmp_path, channel, compressible_image):
        path = save_stack_page(compressible_image, tmp_path, channel, level=0)

        with tifffile.TiffFile(path) as tif:
            assert tif.pages[0].compression == tifffile.COMPRESSION.NONE

    def test_compressed_and_lossless(self, tmp_path, channel, compressible_image):
        path = save_stack_page(compressible_image, tmp_path, channel, level=6)

        with tifffile.TiffFile(path) as tif:
            page = tif.pages[0]
            assert page.compression == tifffile.COMPRESSION.ADOBE_DEFLATE
            assert page.predictor == tifffile.PREDICTOR.HORIZONTAL
            # The channel name in PageName (285) and the JSON metadata must survive compression.
            assert page.tags[285].value.rstrip("\x00") == channel.name
        np.testing.assert_array_equal(tifffile.imread(path), compressible_image)


class TestDiskUsageEstimate:
    """The planner measures the size of one saved test frame, so it has to save it the way the acquisition will."""

    @staticmethod
    def _measured_size(image) -> int:
        frame = SimpleNamespace(frame=image, is_color=lambda: False)
        camera = MagicMock()
        camera.get_is_streaming.return_value = True
        camera.get_callbacks_enabled.return_value = False
        camera.read_camera_frame.return_value = frame
        planner = AcquisitionPlanner(
            objective_store=MagicMock(), channel_config_manager=MagicMock(), camera_service=camera
        )
        return planner._measure_image_size(SimpleNamespace(name="BF LED matrix full"))

    def test_estimate_follows_the_compression_level(self, monkeypatch, compressible_image):
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 0)
        uncompressed = self._measured_size(compressible_image)
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 9)
        compressed = self._measured_size(compressible_image)

        assert compressed < uncompressed

    def test_estimate_ignores_the_level_for_ome_tiff(self, monkeypatch, compressible_image):
        """OME-TIFF is written uncompressed whatever the level, so the estimate must not assume compression."""
        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.OME_TIFF)
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 0)
        uncompressed = self._measured_size(compressible_image)
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", 9)

        assert self._measured_size(compressible_image) == uncompressed
