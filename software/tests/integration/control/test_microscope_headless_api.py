"""Integration tests for headless Microscope API methods (ports upstream 44c4f518)."""

import pytest
import numpy as np


@pytest.mark.integration
def test_set_illumination_intensity_invalid_channel_raises(simulated_microscope):
    """Unknown channel name should raise ValueError, not silently no-op."""
    with pytest.raises(ValueError, match="Channel 'NOT_A_CHANNEL' not found"):
        simulated_microscope.set_illumination_intensity("NOT_A_CHANNEL", 50.0)


@pytest.mark.integration
def test_set_exposure_time_invalid_channel_raises(simulated_microscope):
    """Unknown channel name should raise ValueError, not silently no-op."""
    with pytest.raises(ValueError, match="Channel 'NOT_A_CHANNEL' not found"):
        simulated_microscope.set_exposure_time("NOT_A_CHANNEL", 25.0)


@pytest.mark.integration
def test_save_image_uint16_uses_tiff(simulated_microscope, tmp_path):
    """uint16 images should be saved as .tiff regardless of input extension."""
    image = np.zeros((128, 128), dtype=np.uint16)
    saved = simulated_microscope.save_image(image, str(tmp_path / "out.bmp"))
    assert saved.endswith(".tiff"), f"expected .tiff extension, got {saved}"

    from pathlib import Path

    assert Path(saved).exists()


@pytest.mark.integration
def test_save_image_creates_parent_dir(simulated_microscope, tmp_path):
    """save_image should mkdir parent directories as needed."""
    image = np.zeros((64, 64), dtype=np.uint16)
    nested = tmp_path / "deep" / "nested" / "path"
    saved = simulated_microscope.save_image(image, str(nested / "img"))

    from pathlib import Path

    assert Path(saved).exists()
    assert Path(saved).parent == nested


@pytest.mark.integration
def test_acquire_single_fov_invalid_z_stacking_config_raises(simulated_microscope, tmp_path):
    """Unknown z_stacking_config should raise ValueError."""
    with pytest.raises(ValueError, match="Invalid z_stacking_config"):
        simulated_microscope.acquire_single_fov(
            channel_names=[],
            save_path=str(tmp_path),
            NZ=1,
            z_stacking_config="INVALID",
        )


@pytest.mark.integration
def test_acquire_single_fov_unknown_channel_raises(simulated_microscope, tmp_path):
    """Unknown channel name should raise ValueError before any acquisition."""
    with pytest.raises(ValueError, match="Channel 'NOT_REAL' not found"):
        simulated_microscope.acquire_single_fov(
            channel_names=["NOT_REAL"],
            save_path=str(tmp_path),
            NZ=1,
        )
