"""Utilities for writing OME-TIFF stacks via tifffile memmaps."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

import squid.core.utils.hardware_utils as utils

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from squid.backend.controllers.multipoint.job_processing import CaptureInfo, AcquisitionInfo

# Constants for metadata keys
WRITTEN_INDICES_KEY = "written_indices"
PLANES_KEY = "planes"
SAVED_COUNT_KEY = "saved_count"
EXPECTED_COUNT_KEY = "expected_count"
COMPLETED_KEY = "completed"
START_TIME_KEY = "start_time"
DTYPE_KEY = "dtype"
SHAPE_KEY = "shape"
AXES_KEY = "axes"
CHANNEL_NAMES_KEY = "channel_names"
TIME_INCREMENT_KEY = "time_increment"
TIME_INCREMENT_UNIT_KEY = "time_increment_unit"
PHYSICAL_SIZE_Z_KEY = "physical_size_z"
PHYSICAL_SIZE_Z_UNIT_KEY = "physical_size_z_unit"
PHYSICAL_SIZE_X_KEY = "physical_size_x"
PHYSICAL_SIZE_X_UNIT_KEY = "physical_size_x_unit"
PHYSICAL_SIZE_Y_KEY = "physical_size_y"
PHYSICAL_SIZE_Y_UNIT_KEY = "physical_size_y_unit"


def ome_output_folder(acq_info: "AcquisitionInfo", info: "CaptureInfo") -> str:
    base_dir = acq_info.experiment_path or os.path.dirname(info.save_directory)
    return os.path.join(base_dir, "ome_tiff")


def metadata_temp_path(acq_info: "AcquisitionInfo", info: "CaptureInfo", base_name: str) -> str:
    # Legacy OS-temp tracker location. Kept as a READ-ONLY migration source so an
    # FOV started under the pre-B1.4 build can still be resumed; the live path
    # writes/reads plane_tracker_path() instead.
    base_identifier = acq_info.experiment_path or info.save_directory
    key = f"{base_identifier}:{base_name}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    # Use squid_ome_ prefix to avoid conflicts with other OME-TIFF applications
    return os.path.join(tempfile.gettempdir(), f"squid_ome_{digest}_metadata.json")


def plane_tracker_path(acq_info: "AcquisitionInfo", info: "CaptureInfo", base_name: str) -> str:
    """B1.4: primary plane-tracker path, co-located with the data.

    <experiment>/ome_tiff/.plane_trackers/{base}_metadata.json -- survives OS-temp
    cleanup and travels with the experiment folder for crash-resume.
    """
    return os.path.join(
        ome_output_folder(acq_info, info), ".plane_trackers", f"{base_name}_metadata.json"
    )


def load_metadata(metadata_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(metadata_path):
        return None
    with open(metadata_path, "r", encoding="utf-8") as metadata_file:
        return json.load(metadata_file)


def write_metadata(metadata_path: str, metadata: Dict[str, Any]) -> None:
    # Route through the shared atomic helper so a crash mid-write can never leave
    # a torn tracker (what makes the tracker trustworthy on resume), and so the
    # .plane_trackers parent dir is auto-created.
    from squid.core.utils.atomic_io import atomic_write_json

    atomic_write_json(metadata_path, metadata)


def memmap_matches(output_path: str, expected_shape, expected_dtype) -> bool:
    """B1.4: True iff the existing OME-TIFF memmap has the expected geometry.

    The gate that lets the writer ADOPT (not wipe) a partially-written stack
    when its tracker is missing. Returns False on any read error.
    """
    import tifffile

    try:
        arr = tifffile.memmap(output_path, mode="r")
    except Exception:
        return False
    try:
        if np.dtype(arr.dtype) != np.dtype(expected_dtype):
            return False
        # tifffile may squeeze singleton axes, so arr.shape need not equal SHAPE;
        # the stack matches if it holds the same number of elements (the writer
        # reshapes the memmap to SHAPE before indexing planes).
        return int(arr.size) == int(np.prod([int(s) for s in expected_shape]))
    finally:
        _close_memmap(arr)


def rebuild_written_indices_from_memmap(
    output_path: str, metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """B1.4: repopulate WRITTEN_INDICES/SAVED_COUNT from the non-empty planes of
    an existing memmap so a resume knows which planes are already durable.

    Keys are T-C-Z (matching job_processing.index_key and update_plane_metadata's
    plane_key). PLANES metadata cannot be reconstructed from pixels, so it is
    left as-is. A legitimately all-zero plane reads as 'not written' -- safe and
    idempotent (it will be re-acquired/overwritten).
    """
    import tifffile

    shape = tuple(int(s) for s in metadata[SHAPE_KEY])  # [T, Z, C, Y, X]
    t_n, z_n, c_n = shape[0], shape[1], shape[2]
    arr = tifffile.memmap(output_path, mode="r")
    try:
        # tifffile may squeeze singleton axes; reshape back to the logical
        # TZCYX layout (same buffer order the writer wrote) before indexing.
        view = arr.reshape(shape)
        written: List[str] = []
        for t in range(t_n):
            for z in range(z_n):
                for c in range(c_n):
                    if np.any(view[t, z, c] != 0):
                        written.append(f"{t}-{c}-{z}")
    finally:
        _close_memmap(arr)
    metadata[WRITTEN_INDICES_KEY] = written
    metadata[SAVED_COUNT_KEY] = len(written)
    return metadata


def _close_memmap(arr) -> None:
    """Best-effort release of a numpy memmap's file handle (matters on Windows
    before a subsequent os.remove / re-open of the same file)."""
    try:
        mmap = getattr(arr, "_mmap", None)
        if mmap is not None:
            mmap.close()
    except Exception:  # pragma: no cover - defensive
        pass


def ome_base_name(info: "CaptureInfo") -> str:
    # Import here to avoid circular dependency: _def -> utils_ome_tiff_writer -> _def
    import _def

    return f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}"


def validate_capture_info(info: "CaptureInfo", acq_info: "AcquisitionInfo", image: np.ndarray) -> None:
    """Validate that capture info and acquisition info have required fields for OME-TIFF saving.

    Note: The caller (SaveOMETiffJob.run) is responsible for checking that acq_info is not None.
    The acq_info fields total_time_points, total_z_levels, and total_channels are required (non-Optional)
    per the AcquisitionInfo dataclass definition.
    """
    if info.time_point is None:
        raise ValueError("CaptureInfo.time_point is required for OME-TIFF saving")
    if image.ndim != 2:
        raise NotImplementedError(
            "OME-TIFF saving currently supports 2D grayscale images only"
        )


def initialize_metadata(acq_info: "AcquisitionInfo", info: "CaptureInfo", image: np.ndarray) -> Dict[str, Any]:
    channel_names = acq_info.channel_names or []
    time_increment = (
        float(acq_info.time_increment_s) if acq_info.time_increment_s is not None else None
    )
    time_increment_unit = "s" if time_increment is not None else None
    physical_size_z = (
        float(acq_info.physical_size_z_um) if acq_info.physical_size_z_um is not None else None
    )
    physical_size_z_unit = "µm" if physical_size_z is not None else None
    physical_size_x = (
        float(acq_info.physical_size_x_um) if acq_info.physical_size_x_um is not None else None
    )
    physical_size_x_unit = "µm" if physical_size_x is not None else None
    physical_size_y = (
        float(acq_info.physical_size_y_um) if acq_info.physical_size_y_um is not None else None
    )
    physical_size_y_unit = "µm" if physical_size_y is not None else None
    return {
        DTYPE_KEY: np.dtype(image.dtype).str,
        AXES_KEY: "TZCYX",
        SHAPE_KEY: [
            int(acq_info.total_time_points),
            int(acq_info.total_z_levels),
            int(acq_info.total_channels),
            int(image.shape[-2]),
            int(image.shape[-1]),
        ],
        CHANNEL_NAMES_KEY: channel_names,
        WRITTEN_INDICES_KEY: [],
        SAVED_COUNT_KEY: 0,
        EXPECTED_COUNT_KEY: int(acq_info.total_time_points)
        * int(acq_info.total_z_levels)
        * int(acq_info.total_channels),
        PLANES_KEY: {},
        START_TIME_KEY: info.capture_time,
        COMPLETED_KEY: False,
        TIME_INCREMENT_KEY: time_increment,
        TIME_INCREMENT_UNIT_KEY: time_increment_unit,
        PHYSICAL_SIZE_Z_KEY: physical_size_z,
        PHYSICAL_SIZE_Z_UNIT_KEY: physical_size_z_unit,
        PHYSICAL_SIZE_X_KEY: physical_size_x,
        PHYSICAL_SIZE_X_UNIT_KEY: physical_size_x_unit,
        PHYSICAL_SIZE_Y_KEY: physical_size_y,
        PHYSICAL_SIZE_Y_UNIT_KEY: physical_size_y_unit,
    }


def update_plane_metadata(
    metadata: Dict[str, Any], info: "CaptureInfo"
) -> Dict[str, Any]:
    plane_key = f"{info.time_point}-{info.configuration_idx}-{info.z_index}"
    plane_data: Dict[str, Any] = {
        "TheT": int(info.time_point),
        "TheZ": int(info.z_index),
        "TheC": int(info.configuration_idx),
    }
    if info.position is not None:
        if getattr(info.position, "x_mm", None) is not None:
            plane_data["PositionX"] = float(info.position.x_mm)
            plane_data["PositionXUnit"] = "mm"
        if getattr(info.position, "y_mm", None) is not None:
            plane_data["PositionY"] = float(info.position.y_mm)
            plane_data["PositionYUnit"] = "mm"

    stepper_z_um: Optional[float] = None
    if info.position is not None and getattr(info.position, "z_mm", None) is not None:
        stepper_z_um = float(info.position.z_mm) * 1000.0

    piezo_z_um = float(info.z_piezo_um) if info.z_piezo_um is not None else None
    if metadata.get(START_TIME_KEY) is not None and info.capture_time is not None:
        plane_data["DeltaT"] = float(info.capture_time - metadata[START_TIME_KEY])
    metadata.setdefault(PLANES_KEY, {})[plane_key] = plane_data

    if stepper_z_um is not None or piezo_z_um is not None:
        total_z_um = (stepper_z_um or 0.0) + (piezo_z_um or 0.0)
        plane_data["PositionZ"] = total_z_um
        plane_data["PositionZUnit"] = "µm"

    return metadata


def metadata_for_imwrite(metadata: Dict[str, Any]) -> Dict[str, Any]:
    channel_names = metadata.get(CHANNEL_NAMES_KEY) or []
    meta: Dict[str, Any] = {"axes": "TZCYX"}
    if channel_names:
        meta["Channel"] = {"Name": channel_names}
    if metadata.get(TIME_INCREMENT_KEY) is not None:
        meta["TimeIncrement"] = float(metadata[TIME_INCREMENT_KEY])
        meta["TimeIncrementUnit"] = metadata.get(TIME_INCREMENT_UNIT_KEY, "s")
    if metadata.get(PHYSICAL_SIZE_Z_KEY) is not None:
        meta["PhysicalSizeZ"] = float(metadata[PHYSICAL_SIZE_Z_KEY])
        meta["PhysicalSizeZUnit"] = metadata.get(PHYSICAL_SIZE_Z_UNIT_KEY, "µm")
    if metadata.get(PHYSICAL_SIZE_X_KEY) is not None:
        meta["PhysicalSizeX"] = float(metadata[PHYSICAL_SIZE_X_KEY])
        meta["PhysicalSizeXUnit"] = metadata.get(PHYSICAL_SIZE_X_UNIT_KEY, "µm")
    if metadata.get(PHYSICAL_SIZE_Y_KEY) is not None:
        meta["PhysicalSizeY"] = float(metadata[PHYSICAL_SIZE_Y_KEY])
        meta["PhysicalSizeYUnit"] = metadata.get(PHYSICAL_SIZE_Y_UNIT_KEY, "µm")
    return meta


def build_base_ome_xml(metadata: Dict[str, Any]) -> str:
    import xml.etree.ElementTree as ET

    ns = "http://www.openmicroscopy.org/Schemas/OME/2016-06"
    ET.register_namespace("", ns)

    dtype_map = {
        "uint8": "uint8",
        "uint16": "uint16",
        "uint32": "uint32",
        "int8": "int8",
        "int16": "int16",
        "int32": "int32",
        "float32": "float",
        "float64": "double",
    }

    dtype_str = np.dtype(metadata[DTYPE_KEY]).name
    ome_type = dtype_map.get(dtype_str, dtype_str)
    size_t, size_z, size_c, size_y, size_x = metadata[SHAPE_KEY]

    root = ET.Element("{ns}OME".format(ns="{" + ns + "}"), attrib={"Creator": "Squid"})
    image = ET.SubElement(
        root, "{ns}Image".format(ns="{" + ns + "}"), attrib={"ID": "Image:0"}
    )
    if metadata.get(START_TIME_KEY) is not None:
        try:
            acq_time = datetime.fromtimestamp(metadata[START_TIME_KEY]).isoformat()
            image.set("AcquisitionDate", acq_time)
        except Exception:
            pass
    pixels = ET.SubElement(
        image,
        "{ns}Pixels".format(ns="{" + ns + "}"),
        attrib={
            "ID": "Pixels:0",
            "DimensionOrder": "TZCYX",
            "Type": ome_type,
            "SizeT": str(size_t),
            "SizeC": str(size_c),
            "SizeZ": str(size_z),
            "SizeY": str(size_y),
            "SizeX": str(size_x),
        },
    )

    channel_names = metadata.get(CHANNEL_NAMES_KEY) or []
    if not channel_names:
        channel_names = [f"Channel {idx}" for idx in range(size_c)]

    for idx, name in enumerate(channel_names):
        ET.SubElement(
            pixels,
            "{ns}Channel".format(ns="{" + ns + "}"),
            attrib={
                "ID": f"Channel:0:{idx}",
                "Name": name,
                "SamplesPerPixel": "1",
            },
        )

    xml_body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>' + xml_body


def augment_ome_xml(existing_xml: Optional[str], metadata: Dict[str, Any]) -> str:
    import xml.etree.ElementTree as ET

    if existing_xml:
        root = ET.fromstring(existing_xml)
    else:
        existing_xml = build_base_ome_xml(metadata)
        root = ET.fromstring(existing_xml)

    ns = {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
    ET.register_namespace("", ns["ome"])

    image = root.find("ome:Image", ns)
    if image is None:
        return existing_xml or ""

    if metadata.get(START_TIME_KEY) is not None:
        try:
            acq_time = datetime.fromtimestamp(metadata[START_TIME_KEY]).isoformat()
            image.set("AcquisitionDate", acq_time)
        except Exception:
            pass

    pixels = image.find("ome:Pixels", ns)
    if pixels is None:
        return existing_xml or ""

    if metadata.get(TIME_INCREMENT_KEY) is not None:
        pixels.set("TimeIncrement", str(metadata[TIME_INCREMENT_KEY]))
        pixels.set("TimeIncrementUnit", metadata.get(TIME_INCREMENT_UNIT_KEY, "s"))
    if metadata.get(PHYSICAL_SIZE_Z_KEY) is not None:
        pixels.set("PhysicalSizeZ", str(metadata[PHYSICAL_SIZE_Z_KEY]))
        pixels.set("PhysicalSizeZUnit", metadata.get(PHYSICAL_SIZE_Z_UNIT_KEY, "µm"))
    if metadata.get(PHYSICAL_SIZE_X_KEY) is not None:
        pixels.set("PhysicalSizeX", str(metadata[PHYSICAL_SIZE_X_KEY]))
        pixels.set("PhysicalSizeXUnit", metadata.get(PHYSICAL_SIZE_X_UNIT_KEY, "µm"))
    if metadata.get(PHYSICAL_SIZE_Y_KEY) is not None:
        pixels.set("PhysicalSizeY", str(metadata[PHYSICAL_SIZE_Y_KEY]))
        pixels.set("PhysicalSizeYUnit", metadata.get(PHYSICAL_SIZE_Y_UNIT_KEY, "µm"))

    channel_names = metadata.get(CHANNEL_NAMES_KEY) or []
    if channel_names:
        existing_channels = list(pixels.findall("ome:Channel", ns))
        if len(existing_channels) == len(channel_names):
            for elem, name in zip(existing_channels, channel_names):
                elem.set("Name", name)
        else:
            for elem in existing_channels:
                pixels.remove(elem)
            for idx, name in enumerate(channel_names):
                ET.SubElement(
                    pixels,
                    "{http://www.openmicroscopy.org/Schemas/OME/2016-06}Channel",
                    attrib={
                        "ID": f"Channel:0:{idx}",
                        "Name": name,
                        "SamplesPerPixel": "1",
                    },
                )

    for elem in list(pixels.findall("ome:Plane", ns)):
        pixels.remove(elem)

    planes = metadata.get(PLANES_KEY, {})
    ordered_planes = sorted(
        planes.values(),
        key=lambda p: (p.get("TheT", 0), p.get("TheC", 0), p.get("TheZ", 0)),
    )

    for plane in ordered_planes:
        ET.SubElement(
            pixels,
            "{http://www.openmicroscopy.org/Schemas/OME/2016-06}Plane",
            attrib={key: str(value) for key, value in plane.items()},
        )

    xml_body = ET.tostring(root, encoding="unicode")
    if not xml_body.startswith("<?xml"):
        xml_body = '<?xml version="1.0" encoding="UTF-8"?>' + xml_body
    return xml_body


def ensure_output_directory(path: str) -> None:
    utils.ensure_directory_exists(path)


def cleanup_stale_metadata_files() -> List[str]:
    """Sweep redundant COMPLETED trackers from the LEGACY OS-temp location only.

    B1.4: trackers are now co-located with the data (plane_tracker_path); this
    janitor never touches .plane_trackers. An INCOMPLETE legacy tracker is
    resume/migration fuel, so it is KEPT (previously it was deleted -- the exact
    file a migrating resume needs). Only COMPLETED (redundant) legacy trackers
    are removed. Returns a list of removed file paths.
    """
    pattern = os.path.join(tempfile.gettempdir(), "squid_ome_*_metadata.json")
    stale_files = glob.glob(pattern)
    removed = []
    for filepath in stale_files:
        try:
            # Incomplete trackers are resume fuel -- keep them. Only remove a
            # COMPLETED (redundant) legacy tracker.
            metadata = load_metadata(filepath)
            if metadata is not None and metadata.get(COMPLETED_KEY, False):
                os.remove(filepath)
                removed.append(filepath)
                # Also try to remove the lock file
                lock_path = filepath + ".lock"
                if os.path.exists(lock_path):
                    try:
                        os.remove(lock_path)
                        removed.append(lock_path)
                    except OSError:
                        pass
        except Exception:
            # If we can't read or remove it, skip silently
            pass
    return removed
