import numpy as np
import tifffile

from squid.backend.io.writers.single_capture_ome_writer import (
    CapturePlane, write_multichannel_ome_tiff, next_capture_path,
)


def test_writes_cyx_stack(tmp_path):
    planes = [
        CapturePlane("405", np.zeros((4, 6), np.uint16), "#7C4DFF", 40.0, 405),
        CapturePlane("488", np.ones((4, 6), np.uint16), "#22D3EE", 20.0, 488),
    ]
    out = tmp_path / "slideA_000.ome.tiff"
    write_multichannel_ome_tiff(str(out), planes)
    arr = tifffile.imread(str(out))
    assert arr.shape == (2, 4, 6) and arr.dtype == np.uint16


def test_metadata_has_channel_names(tmp_path):
    planes = [CapturePlane("405", np.zeros((2, 2), np.uint16), "#7C4DFF", 40.0, 405),
              CapturePlane("BF", np.zeros((2, 2), np.uint16), "#FFFFFF", 5.0, None)]
    out = tmp_path / "x_000.ome.tiff"
    write_multichannel_ome_tiff(str(out), planes)
    with tifffile.TiffFile(str(out)) as tf:
        xml = tf.ome_metadata or ""
    assert "405" in xml and "BF" in xml


def test_next_capture_path_numbering(tmp_path):
    assert next_capture_path(str(tmp_path), "slideA").endswith("slideA_000.ome.tiff")
    (tmp_path / "slideA_000.ome.tiff").write_bytes(b"")
    assert next_capture_path(str(tmp_path), "slideA").endswith("slideA_001.ome.tiff")


def test_next_capture_path_sanitizes_blank_prefix(tmp_path):
    assert next_capture_path(str(tmp_path), "").endswith("capture_000.ome.tiff")
