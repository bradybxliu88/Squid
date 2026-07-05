"""B1.3(2) tests: durable coordinates.csv (incremental append + resume preload).

CoordinateTracker gains append_new_since_cursor() (per-FOV durability) and
preload_from_csv() (resume reconcile, CORRECTION C3). The end-of-timepoint
mode='w' rewrite must reconcile pre-crash + post-resume rows, never clobber.
"""

from dataclasses import dataclass

import pandas as pd

from squid.backend.controllers.multipoint.progress_tracking import CoordinateTracker


@dataclass
class FakePos:
    x_mm: float = 1.0
    y_mm: float = 2.0
    z_mm: float = 0.05


def _record_fov(tracker, fov, n_z=2):
    for z in range(n_z):
        tracker.record(
            region_id="A1", z_level=z, pos=FakePos(), fov=fov, fov_id=f"A1_{fov:04d}"
        )


def test_append_writes_incrementally_and_is_idempotent(tmp_path):
    path = str(tmp_path / "coordinates.csv")
    tracker = CoordinateTracker(use_piezo=False)
    tracker.initialize()

    _record_fov(tracker, 0, n_z=3)
    tracker.append_new_since_cursor(path)
    assert len(pd.read_csv(path)) == 3
    assert tracker._flush_cursor == 3

    _record_fov(tracker, 1, n_z=2)
    tracker.append_new_since_cursor(path)
    df = pd.read_csv(path)
    assert len(df) == 5  # header NOT duplicated, only 2 new rows added

    # No new rows -> a no-op write.
    tracker.append_new_since_cursor(path)
    assert len(pd.read_csv(path)) == 5


def test_preload_reconciles_instead_of_clobbering(tmp_path):
    path = str(tmp_path / "coordinates.csv")

    # Pre-crash: 2 rows persisted.
    t1 = CoordinateTracker(use_piezo=False)
    t1.initialize()
    _record_fov(t1, 0, n_z=2)
    t1.save(path)
    assert len(pd.read_csv(path)) == 2

    # Resume: fresh tracker preloads the existing file.
    t2 = CoordinateTracker(use_piezo=False)
    t2.initialize()
    t2.preload_from_csv(path)
    assert len(t2._coordinates_df) == 2
    assert t2._flush_cursor == 2

    # Post-resume FOV records + appends only the NEW rows.
    _record_fov(t2, 1, n_z=3)
    t2.append_new_since_cursor(path)
    assert len(pd.read_csv(path)) == 5  # 2 preloaded + 3 new (no re-append of the 2)

    # Authoritative end-of-timepoint rewrite reconciles (no clobber).
    t2.save(path)
    df = pd.read_csv(path)
    assert len(df) == 5
    assert set(df["fov_id"]) == {"A1_0000", "A1_0001"}


def test_incremental_final_file_matches_single_save(tmp_path):
    # Normal-path invariance: per-FOV append + final save() produces the exact
    # same coordinates.csv as a single save() at the end.
    control_path = str(tmp_path / "control.csv")
    control = CoordinateTracker(use_piezo=False)
    control.initialize()
    for fov in range(2):
        _record_fov(control, fov, n_z=2)
    control.save(control_path)

    inc_path = str(tmp_path / "inc.csv")
    inc = CoordinateTracker(use_piezo=False)
    inc.initialize()
    for fov in range(2):
        _record_fov(inc, fov, n_z=2)
        inc.append_new_since_cursor(inc_path)
    inc.save(inc_path)

    # Same rows/columns/order (the per-row "time" stamp differs by wall clock,
    # so drop it before comparing structure and coordinates).
    control_df = pd.read_csv(control_path).drop(columns=["time"])
    inc_df = pd.read_csv(inc_path).drop(columns=["time"])
    assert list(control_df.columns) == list(inc_df.columns)
    assert control_df.equals(inc_df)


def test_preload_absent_file_is_noop(tmp_path):
    tracker = CoordinateTracker(use_piezo=False)
    tracker.initialize()
    tracker.preload_from_csv(str(tmp_path / "does_not_exist.csv"))
    assert tracker._flush_cursor == 0
    assert len(tracker._coordinates_df) == 0
