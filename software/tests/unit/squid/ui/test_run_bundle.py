"""Unit tests for the Qt-free run-bundle helpers (save/load a portable folder).

The headline test is the round-trip: build a source bundle, plan + execute the
copy, then prove the exported folder RE-LOADS via ProtocolLoader with every
resource resolving to the copied file (i.e. the bundle is self-contained).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from squid.core.protocol import ProtocolLoader
from squid.ui.widgets.orchestrator.run_bundle import (
    collect_bundle_refs,
    dest_within_target,
    find_bundle_protocol,
    find_bundle_protocols,
    plan_bundle_copy,
)


def _write_bundle(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "fov_positions.csv").write_text("region,x (mm),y (mm)\n5,1.0,2.0\n")
    (d / "MERFISH_config.json").write_text("{}")
    (d / "fluidics_protocols.yaml").write_text("wash: {}\n")
    (d / "imaging_4ch.yaml").write_text("channels: [BF]\n")
    protocol = d / "protocol.yaml"
    protocol.write_text(
        "\n".join(
            [
                "name: Bundle RT",
                'version: "3.0"',
                "output_directory: outputs",
                "resources:",
                "  fov_file: fov_positions.csv",
                "  fluidics_config_file: MERFISH_config.json",
                "  fluidics_protocols_file: fluidics_protocols.yaml",
                "rounds:",
                "  - name: Round 1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: imaging_4ch.yaml",
            ]
        )
    )
    return protocol


# --------------------------------------------------------------- collect refs
def test_collect_bundle_refs_gathers_and_dedups():
    raw = {
        "resources": {
            "fov_file": "fov.csv",
            "fluidics_config_file": "cfg.json",
            "fluidics_protocols_file": "fl.yaml",
        },
        "rounds": [
            {
                "steps": [
                    {"step_type": "imaging", "protocol": "img.yaml"},
                    {"step_type": "fluidics", "protocol": "wash"},  # named -> ignored
                    {"step_type": "imaging", "protocol": "img.yaml"},  # dup -> ignored
                ]
            }
        ],
    }
    assert collect_bundle_refs(raw) == ["fl.yaml", "cfg.json", "fov.csv", "img.yaml"]


def test_collect_bundle_refs_ignores_named_imaging_protocols():
    raw = {"rounds": [{"steps": [{"step_type": "imaging", "protocol": "standard"}]}]}
    assert collect_bundle_refs(raw) == []  # "standard" is a named ref, not a file


def test_collect_bundle_refs_tolerates_legacy_top_level():
    assert collect_bundle_refs({"fov_file": "fov.csv", "rounds": []}) == ["fov.csv"]


def test_collect_bundle_refs_handles_non_dict():
    assert collect_bundle_refs(None) == []
    assert collect_bundle_refs([]) == []


def test_collect_bundle_refs_gathers_inline_file_protocols():
    # Named protocols given as {name: {file: <path>}} must be bundled too — the
    # loader resolves and requires them just like fov_file.
    raw = {
        "resources": {
            "imaging_protocols": {"standard": {"file": "imaging/std.yaml"}},
            "fluidics_protocols": {"wash": {"file": "fluidics/wash.yaml"}},
        },
        "rounds": [],
    }
    assert collect_bundle_refs(raw) == ["imaging/std.yaml", "fluidics/wash.yaml"]


def test_collect_bundle_refs_skips_inline_channels_only_protocols():
    # An inline protocol defined by value (channels:, not file:) has nothing to
    # copy and must not be treated as a file ref.
    raw = {
        "resources": {"imaging_protocols": {"standard": {"channels": ["BF"]}}},
        "rounds": [],
    }
    assert collect_bundle_refs(raw) == []


def test_collect_bundle_refs_gathers_legacy_top_level_group_files():
    # The loader hoists a legacy TOP-LEVEL imaging_protocols/fluidics_protocols
    # block into resources and requires each {file:} — so bundling must collect
    # them from the top level too, not only from under resources:.
    raw = {
        "imaging_protocols": {"std": {"file": "sub/std.yaml"}},
        "fluidics_protocols": {"wash": {"file": "fl/wash.yaml"}},
        "rounds": [{"steps": [{"step_type": "imaging", "protocol": "std"}]}],
    }
    assert collect_bundle_refs(raw) == ["sub/std.yaml", "fl/wash.yaml"]


# ------------------------------------------------------------- find protocol
def test_find_bundle_protocol_picks_rounds_yaml(tmp_path):
    (tmp_path / "imaging.yaml").write_text("channels: [BF]\n")
    (tmp_path / "protocol.yaml").write_text(
        "name: P\nversion: '3.0'\nrounds:\n  - name: R1\n    steps: []\n"
    )
    assert find_bundle_protocol(str(tmp_path)) == str(tmp_path / "protocol.yaml")


def test_find_bundle_protocol_none_when_absent(tmp_path):
    (tmp_path / "imaging.yaml").write_text("channels: [BF]\n")
    assert find_bundle_protocol(str(tmp_path)) is None


def test_find_bundle_protocols_returns_all_rounds_yamls(tmp_path):
    (tmp_path / "imaging.yaml").write_text("channels: [BF]\n")  # not a protocol
    (tmp_path / "a_protocol.yaml").write_text(
        "name: A\nversion: '3.0'\nrounds:\n  - name: R1\n    steps: []\n"
    )
    (tmp_path / "b_protocol.yml").write_text(
        "name: B\nversion: '3.0'\nrounds:\n  - name: R1\n    steps: []\n"
    )
    found = find_bundle_protocols(str(tmp_path))
    assert [Path(p).name for p in found] == ["a_protocol.yaml", "b_protocol.yml"]
    # The single-picker returns the first of these.
    assert find_bundle_protocol(str(tmp_path)) == found[0]


# ------------------------------------------------------------------- planning
def test_plan_flags_missing_external_and_same_dir(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "fov.csv").write_text("region,x,y\n5,1,2\n")
    ext = tmp_path / "ext.json"
    ext.write_text("{}")
    protocol = src / "p.yaml"
    protocol.write_text(
        "\n".join(
            [
                "name: P",
                'version: "3.0"',
                "resources:",
                "  fov_file: fov.csv",
                f"  fluidics_config_file: {str(ext)}",  # absolute -> external
                "  fluidics_protocols_file: missing.yaml",  # absent -> missing
                "rounds:",
                "  - name: R1",
                "    steps:",
                "      - step_type: intervention",
                "        message: hi",
            ]
        )
    )

    plan = plan_bundle_copy(str(protocol), str(tmp_path / "out"))
    assert "missing.yaml" in plan["missing"]
    assert str(ext) in plan["external"]
    assert any(s.endswith("fov.csv") for s, _ in plan["files"])
    assert plan["same_dir"] is False

    # Saving into the protocol's own folder is a no-op.
    assert plan_bundle_copy(str(protocol), str(src))["same_dir"] is True


def test_plan_flattens_dotdot_ref_inside_target_and_flags_external(tmp_path):
    # A relative ref that climbs out of the protocol dir ('../shared/fov.csv')
    # must NOT be written outside the target folder. It is flattened to
    # target/<basename> and reported as external (bundle not self-contained).
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "fov.csv").write_text("region,x,y\n5,1,2\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    protocol = proj / "p.yaml"
    protocol.write_text(
        "\n".join(
            [
                "name: P",
                'version: "3.0"',
                "resources:",
                "  fov_file: ../shared/fov.csv",
                "rounds:",
                "  - name: R1",
                "    steps: []",
            ]
        )
    )
    target = tmp_path / "out"
    plan = plan_bundle_copy(str(protocol), str(target))

    assert "../shared/fov.csv" in plan["external"]
    dests = [dest for _src, dest in plan["files"]]
    assert len(dests) == 1
    dest = Path(dests[0])
    # Dest is target/fov.csv — INSIDE target, never target/../shared/fov.csv.
    assert dest == target / "fov.csv"
    target_n = os.path.normcase(os.path.abspath(str(target)))
    dest_n = os.path.normcase(os.path.abspath(str(dest)))
    assert dest_n.startswith(target_n + os.sep)


def test_plan_collision_self_contained_ref_wins_the_dest(tmp_path):
    # A relative ref (fov.csv, self-contained) and an absolute ref
    # (other/fov.csv, external) both map to target/fov.csv. The reloaded protocol
    # text says 'fov_file: fov.csv', which resolves against the bundle folder, so
    # target/fov.csv MUST hold the LOCAL file's bytes. The external ref — a mere
    # courtesy copy, the YAML still points at its original location — must lose,
    # never win and corrupt the relative ref's dest.
    src = tmp_path / "src"
    src.mkdir()
    (src / "fov.csv").write_text("region,x,y\n5,1,2\n")  # the intended bytes
    other = tmp_path / "other"
    other.mkdir()
    (other / "fov.csv").write_text("DIFFERENT\n")  # unrelated content
    protocol = src / "p.yaml"
    protocol.write_text(
        "\n".join(
            [
                "name: P",
                'version: "3.0"',
                "resources:",
                "  fov_file: fov.csv",
                f"  fluidics_config_file: {str(other / 'fov.csv')}",
                "rounds:",
                "  - name: R1",
                "    steps: []",
            ]
        )
    )
    plan = plan_bundle_copy(str(protocol), str(tmp_path / "out"))

    # Exactly one file mapped to the shared dest, and it is the LOCAL one — the
    # external absolute ref is the collision loser (no silent wrong-bytes clobber).
    assert len(plan["files"]) == 1
    winning_src, winning_dest = plan["files"][0]
    assert os.path.samefile(winning_src, src / "fov.csv")
    assert Path(winning_dest).name == "fov.csv"
    assert plan["collisions"] == [str(other / "fov.csv")]


def test_plan_reserves_protocol_dest_against_resource_collision(tmp_path):
    # A resource named the same as the protocol file must not overwrite it.
    src = tmp_path / "src"
    src.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "protocol.yaml").write_text("not the protocol\n")
    protocol = src / "protocol.yaml"
    protocol.write_text(
        "\n".join(
            [
                "name: P",
                'version: "3.0"',
                "resources:",
                f"  fov_file: {str(other / 'protocol.yaml')}",
                "rounds:",
                "  - name: R1",
                "    steps: []",
            ]
        )
    )
    plan = plan_bundle_copy(str(protocol), str(tmp_path / "out"))
    # The resource collides with the reserved protocol dest -> skipped.
    assert str(other / "protocol.yaml") in plan["collisions"]
    assert plan["files"] == []


def test_plan_reports_existing_dests_for_overwrite_confirmation(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "fov.csv").write_text("region,x,y\n5,1,2\n")
    protocol = src / "p.yaml"
    protocol.write_text(
        "\n".join(
            [
                "name: P",
                'version: "3.0"',
                "resources:",
                "  fov_file: fov.csv",
                "rounds:",
                "  - name: R1",
                "    steps: []",
            ]
        )
    )
    target = tmp_path / "out"
    target.mkdir()
    # Pre-existing files at the planned dests -> reported as 'existing'.
    (target / "p.yaml").write_text("old\n")
    (target / "fov.csv").write_text("old\n")

    plan = plan_bundle_copy(str(protocol), str(target))
    existing_names = {Path(p).name for p in plan["existing"]}
    assert existing_names == {"p.yaml", "fov.csv"}


def test_plan_existing_empty_for_fresh_target(tmp_path):
    protocol_path = _write_bundle(tmp_path / "src")
    plan = plan_bundle_copy(str(protocol_path), str(tmp_path / "fresh"))
    assert plan["existing"] == []
    assert plan["collisions"] == []


def test_plan_bundles_legacy_top_level_imaging_protocol_file(tmp_path):
    # A protocol whose imaging_protocols block sits at the TOP LEVEL (not under
    # resources:) must still have its {file:} bundled — otherwise the exported
    # folder silently omits it and fails to reload.
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "std.yaml").write_text("channels: [BF]\n")
    protocol = src / "p.yaml"
    protocol.write_text(
        "\n".join(
            [
                "name: P",
                'version: "3.0"',
                "imaging_protocols:",
                "  std:",
                "    file: sub/std.yaml",
                "rounds:",
                "  - name: R1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: std",
            ]
        )
    )
    plan = plan_bundle_copy(str(protocol), str(tmp_path / "out"))
    assert plan["missing"] == []
    assert any(s.endswith("std.yaml") for s, _ in plan["files"])


def test_dest_within_target_lexical_containment(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    # A normal in-target dest is allowed.
    assert dest_within_target(str(target), str(target / "fov.csv")) is True
    assert dest_within_target(str(target), str(target / "sub" / "fov.csv")) is True
    # A sibling folder is NOT inside target (guards the copy from writing out).
    assert dest_within_target(str(target), str(tmp_path / "other" / "fov.csv")) is False


# ------------------------------------------------------------- the round-trip
def test_bundle_roundtrip_is_self_contained(tmp_path):
    protocol_path = _write_bundle(tmp_path / "src")
    target = tmp_path / "bundle_out"

    plan = plan_bundle_copy(str(protocol_path), str(target))
    assert plan["same_dir"] is False
    assert plan["missing"] == []
    assert plan["external"] == []

    for src, dest in [plan["protocol"], *plan["files"]]:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    for name in (
        "protocol.yaml",
        "fov_positions.csv",
        "MERFISH_config.json",
        "fluidics_protocols.yaml",
        "imaging_4ch.yaml",
    ):
        assert (target / name).exists(), name

    # The exported bundle re-loads, and resources resolve to the COPIED files.
    found = find_bundle_protocol(str(target))
    assert found == str(target / "protocol.yaml")
    loaded = ProtocolLoader().load(found)
    assert os.path.samefile(loaded.fov_file, target / "fov_positions.csv")


def test_bundle_roundtrip_legacy_top_level_imaging_protocol(tmp_path):
    # Regression: a protocol with a TOP-LEVEL imaging_protocols block (loader
    # hoists it into resources) must still export a self-contained bundle that
    # re-loads without a 'file not found' error.
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "std.yaml").write_text("channels: [BF]\n")
    protocol_path = src / "protocol.yaml"
    protocol_path.write_text(
        "\n".join(
            [
                "name: Legacy Top-Level",
                'version: "3.0"',
                "output_directory: outputs",
                "imaging_protocols:",
                "  std:",
                "    file: sub/std.yaml",
                "rounds:",
                "  - name: Round 1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: std",
            ]
        )
    )
    target = tmp_path / "bundle_out"
    plan = plan_bundle_copy(str(protocol_path), str(target))
    assert plan["missing"] == []

    for s, dest in [plan["protocol"], *plan["files"]]:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, dest)

    # The exported bundle re-loads (the referenced sub/std.yaml was bundled).
    found = find_bundle_protocol(str(target))
    ProtocolLoader().load(found)  # raises if sub/std.yaml is missing
