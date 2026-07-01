"""Unit tests for FOVFocusMemory - per-FOV piezo Z position store."""

from __future__ import annotations

import math
import threading

import pytest

from squid.backend.controllers.multipoint.fov_focus_memory import FOVFocusMemory


# ---------------------------------------------------------------------------
# Basic get / set / fallback
# ---------------------------------------------------------------------------


class TestBasicOperations:
    def test_empty_memory_has_no_seed_and_no_entries(self):
        m = FOVFocusMemory()
        assert len(m) == 0
        assert m.get("well_A1", 0) is None
        assert m.get_seed("well_A1", 0) is None
        assert m.default_z_um is None

    def test_default_only_returns_via_seed_not_get(self):
        m = FOVFocusMemory(default_z_um=150.0)
        assert m.get("well_A1", 0) is None  # no cache
        assert m.get_seed("well_A1", 0) == 150.0  # falls back to default

    def test_remember_and_get_roundtrip(self):
        m = FOVFocusMemory()
        m.remember("well_A1", 3, 142.5)
        assert m.get("well_A1", 3) == 142.5
        assert m.get_seed("well_A1", 3) == 142.5
        assert ("well_A1", 3) in m

    def test_remember_overwrites_previous_value(self):
        m = FOVFocusMemory()
        m.remember("r", 0, 100.0)
        m.remember("r", 0, 200.0)
        assert m.get("r", 0) == 200.0

    def test_cached_value_shadows_default(self):
        m = FOVFocusMemory(default_z_um=150.0)
        m.remember("well_A1", 0, 143.2)
        assert m.get_seed("well_A1", 0) == 143.2

    def test_forget_removes_entry(self):
        m = FOVFocusMemory(default_z_um=150.0)
        m.remember("r", 0, 100.0)
        m.forget("r", 0)
        assert m.get("r", 0) is None
        # Seed falls back to default after forget
        assert m.get_seed("r", 0) == 150.0

    def test_forget_missing_is_noop(self):
        m = FOVFocusMemory()
        m.forget("never_existed", 42)  # should not raise

    def test_clear_removes_entries_but_preserves_default(self):
        m = FOVFocusMemory(default_z_um=150.0)
        m.remember("r", 0, 100.0)
        m.remember("r", 1, 101.0)
        m.clear()
        assert len(m) == 0
        assert m.default_z_um == 150.0
        assert m.get_seed("r", 0) == 150.0

    def test_set_default_updates_seed(self):
        m = FOVFocusMemory(default_z_um=150.0)
        m.set_default(200.0)
        assert m.default_z_um == 200.0
        assert m.get_seed("new_region", 0) == 200.0

    def test_set_default_none_clears_seed(self):
        m = FOVFocusMemory(default_z_um=150.0)
        m.set_default(None)
        assert m.default_z_um is None
        assert m.get_seed("new_region", 0) is None

    def test_items_returns_snapshot(self):
        m = FOVFocusMemory()
        m.remember("a", 0, 1.0)
        m.remember("b", 1, 2.0)
        items = dict(m.items())
        assert items == {("a", 0): 1.0, ("b", 1): 2.0}

    def test_contains_only_matches_tuples(self):
        m = FOVFocusMemory()
        m.remember("r", 0, 100.0)
        assert ("r", 0) in m
        assert ("r", 1) not in m
        assert "r" not in m  # wrong type
        assert [("r", 0)] not in m  # wrong type


# ---------------------------------------------------------------------------
# Input validation / error cases
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("bad_region", ["", None, 42, 3.14, ["r"]])
    def test_remember_rejects_invalid_region_id(self, bad_region):
        m = FOVFocusMemory()
        with pytest.raises(ValueError, match="region_id"):
            m.remember(bad_region, 0, 100.0)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_fov", [-1, "0", 1.5, None])
    def test_remember_rejects_invalid_fov_index(self, bad_fov):
        m = FOVFocusMemory()
        with pytest.raises(ValueError, match="fov_index"):
            m.remember("r", bad_fov, 100.0)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_z", [float("nan"), float("inf"), float("-inf"), None, "100"])
    def test_remember_rejects_non_finite_z(self, bad_z):
        m = FOVFocusMemory()
        with pytest.raises(ValueError, match="z_um"):
            m.remember("r", 0, bad_z)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_default", [float("nan"), float("inf")])
    def test_set_default_rejects_non_finite(self, bad_default):
        m = FOVFocusMemory()
        with pytest.raises(ValueError, match="default_z_um"):
            m.set_default(bad_default)

    def test_fov_index_zero_is_valid(self):
        m = FOVFocusMemory()
        m.remember("r", 0, 100.0)
        assert m.get("r", 0) == 100.0


# ---------------------------------------------------------------------------
# Update bulk ops
# ---------------------------------------------------------------------------


class TestBulkUpdate:
    def test_update_inserts_valid_entries(self):
        m = FOVFocusMemory()
        m.update([("a", 0, 1.0), ("b", 1, 2.0), ("c", 2, 3.0)])
        assert len(m) == 3
        assert m.get("a", 0) == 1.0
        assert m.get("b", 1) == 2.0
        assert m.get("c", 2) == 3.0

    def test_update_silently_skips_invalid_entries(self):
        m = FOVFocusMemory()
        m.update(
            [
                ("good", 0, 1.0),
                ("", 1, 2.0),  # bad: empty region
                ("x", -1, 3.0),  # bad: negative fov
                ("y", 0, float("nan")),  # bad: NaN
                ("z", 2, 4.0),  # good
            ]
        )
        assert len(m) == 2
        assert m.get("good", 0) == 1.0
        assert m.get("z", 2) == 4.0

    def test_update_empty_iterable_is_noop(self):
        m = FOVFocusMemory()
        m.update([])
        assert len(m) == 0


# ---------------------------------------------------------------------------
# Serialization roundtrip (for checkpointing)
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_empty_roundtrip(self):
        m = FOVFocusMemory()
        restored = FOVFocusMemory.from_dict(m.to_dict())
        assert len(restored) == 0
        assert restored.default_z_um is None

    def test_full_roundtrip(self):
        m = FOVFocusMemory(default_z_um=150.0)
        m.remember("well_A1", 0, 140.5)
        m.remember("well_A1", 1, 141.0)
        m.remember("well_B2", 0, 155.2)

        data = m.to_dict()
        restored = FOVFocusMemory.from_dict(data)

        assert restored.default_z_um == 150.0
        assert len(restored) == 3
        assert restored.get("well_A1", 0) == 140.5
        assert restored.get("well_A1", 1) == 141.0
        assert restored.get("well_B2", 0) == 155.2

    def test_to_dict_is_json_safe(self):
        import json

        m = FOVFocusMemory(default_z_um=150.0)
        m.remember("r", 0, 100.0)
        # Should not raise
        serialized = json.dumps(m.to_dict())
        loaded = json.loads(serialized)
        restored = FOVFocusMemory.from_dict(loaded)
        assert restored.get("r", 0) == 100.0

    def test_from_dict_none_returns_empty_memory(self):
        m = FOVFocusMemory.from_dict(None)
        assert len(m) == 0
        assert m.default_z_um is None

    def test_from_dict_missing_fields_gives_empty(self):
        m = FOVFocusMemory.from_dict({})
        assert len(m) == 0
        assert m.default_z_um is None

    def test_from_dict_ignores_malformed_keys(self):
        m = FOVFocusMemory.from_dict(
            {
                "default_z_um": 100.0,
                "positions": {
                    "valid::0": 1.0,
                    "no_separator": 2.0,
                    "empty::::1": 3.0,  # still parses (rpartition gives "empty::" region)
                    "nonint_fov::abc": 4.0,
                    "": 5.0,
                    "valid::-5": 6.0,  # negative index rejected
                    "r::0": float("nan"),
                },
            }
        )
        # "valid::0" and "empty::::1" should both parse; nan rejected
        assert m.get("valid", 0) == 1.0
        assert m.get("empty::", 1) == 3.0
        assert ("valid", -5) not in m
        assert ("r", 0) not in m

    def test_from_dict_ignores_non_mapping_positions(self):
        m = FOVFocusMemory.from_dict({"default_z_um": 50.0, "positions": "not a dict"})
        assert m.default_z_um == 50.0
        assert len(m) == 0

    def test_from_dict_accepts_int_z_values(self):
        m = FOVFocusMemory.from_dict(
            {"default_z_um": 100, "positions": {"r::0": 42}}
        )
        assert m.default_z_um == 100.0
        assert m.get("r", 0) == 42.0

    def test_to_dict_with_colons_in_region_id(self):
        """Region IDs containing '::' should still roundtrip via rpartition."""
        m = FOVFocusMemory()
        m.remember("well::A1", 0, 100.0)  # pathological but legal
        restored = FOVFocusMemory.from_dict(m.to_dict())
        # rpartition splits on the LAST "::", so this roundtrips correctly
        assert restored.get("well::A1", 0) == 100.0


# ---------------------------------------------------------------------------
# Thread safety (concurrent reads / writes)
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_writes_preserve_all_entries(self):
        """100 threads each writing 50 unique FOVs — all 5000 should land."""
        m = FOVFocusMemory()
        num_threads = 100
        per_thread = 50
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for fov in range(per_thread):
                    m.remember(f"region_{tid}", fov, float(tid * 1000 + fov))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(m) == num_threads * per_thread
        # Spot-check
        assert m.get("region_42", 7) == 42.0 * 1000 + 7

    def test_concurrent_read_during_write_does_not_crash(self):
        m = FOVFocusMemory(default_z_um=100.0)
        stop = threading.Event()
        errors: list[Exception] = []

        def reader() -> None:
            try:
                while not stop.is_set():
                    for i in range(1000):
                        _ = m.get_seed("r", i % 20)
                        _ = m.items()
            except Exception as e:
                errors.append(e)

        def writer() -> None:
            try:
                for i in range(5000):
                    m.remember("r", i % 20, float(i))
            except Exception as e:
                errors.append(e)

        readers = [threading.Thread(target=reader) for _ in range(4)]
        w = threading.Thread(target=writer)
        for t in readers:
            t.start()
        w.start()
        w.join()
        stop.set()
        for t in readers:
            t.join()

        assert errors == []

    def test_clear_during_writes_is_safe(self):
        m = FOVFocusMemory()
        stop = threading.Event()
        errors: list[Exception] = []

        def clearer() -> None:
            try:
                for _ in range(50):
                    m.clear()
            except Exception as e:
                errors.append(e)

        def writer() -> None:
            try:
                for i in range(5000):
                    m.remember("r", i % 100, float(i))
            except Exception as e:
                errors.append(e)

        c = threading.Thread(target=clearer)
        w = threading.Thread(target=writer)
        c.start()
        w.start()
        c.join()
        w.join()

        assert errors == []


# ---------------------------------------------------------------------------
# Edge-case scaling
# ---------------------------------------------------------------------------


class TestScale:
    def test_large_fov_grid_12_rounds(self):
        """Simulate the user's setup: 25 FOVs × 12 rounds of updates."""
        m = FOVFocusMemory(default_z_um=150.0)
        for round_idx in range(12):
            for fov in range(25):
                # Each round updates the Z (small drift per round)
                z = 150.0 + round_idx * 0.1 + fov * 0.01
                m.remember("region_5", fov, z)
        # After 12 rounds, each FOV has its latest Z
        assert len(m) == 25
        last_round_z = 150.0 + 11 * 0.1 + 24 * 0.01
        assert m.get("region_5", 24) == pytest.approx(last_round_z)

    def test_many_regions_and_fovs(self):
        """Multi-region plate with 8 regions × 50 FOVs each."""
        m = FOVFocusMemory(default_z_um=100.0)
        for region_idx in range(8):
            for fov in range(50):
                m.remember(f"well_{region_idx}", fov, 100.0 + region_idx * 10 + fov * 0.1)
        assert len(m) == 400
        assert m.get("well_3", 17) == pytest.approx(100.0 + 3 * 10 + 17 * 0.1)


# ---------------------------------------------------------------------------
# Finite-checking semantics
# ---------------------------------------------------------------------------


class TestFiniteValues:
    def test_zero_is_valid_z(self):
        m = FOVFocusMemory()
        m.remember("r", 0, 0.0)
        assert m.get("r", 0) == 0.0

    def test_negative_z_is_valid(self):
        """Piezo position might be negative relative to a home point."""
        m = FOVFocusMemory()
        m.remember("r", 0, -25.5)
        assert m.get("r", 0) == -25.5

    def test_very_small_z_is_valid(self):
        m = FOVFocusMemory()
        m.remember("r", 0, 1e-10)
        assert m.get("r", 0) == 1e-10

    def test_very_large_z_is_valid(self):
        m = FOVFocusMemory()
        m.remember("r", 0, 1e10)
        assert m.get("r", 0) == 1e10
