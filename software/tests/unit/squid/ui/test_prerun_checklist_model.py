"""Unit tests for the Qt-free pre-run checklist model."""

from squid.ui.widgets.orchestrator.prerun_checklist_model import (
    AUTO,
    MANUAL,
    SEV_INFO,
    SEV_WARN,
    PreRunChecklistModel,
    default_items,
    format_fluidics_config_note,
    format_fov_freshness_note,
    format_image_format_note,
)


def test_default_items_shape():
    model = PreRunChecklistModel()
    items = model.items()
    assert len(items) == 14  # 8 auto + 6 manual
    autos = [i for i in items if i.kind == AUTO]
    manuals = [i for i in items if i.kind == MANUAL]
    assert len(autos) == 8
    assert len(manuals) == 6
    # Only reliably-observed items are required: protocol loaded/validated + FOVs
    # (3 auto) plus the 6 physical manual items = 9. The rest are informational.
    required = model.required_items()
    assert len(required) == 9
    for informational in ("fluidics_loaded", "output_path", "objective_selected",
                          "laser_af_reference", "focus_locked"):
        assert not model.get(informational).required, informational
    for req in ("protocol_loaded", "protocol_validated", "fov_present"):
        assert model.get(req).required, req


def test_set_auto_flips_and_reports_change():
    model = PreRunChecklistModel()
    assert model.set_auto("protocol_loaded", True) is True
    assert model.is_checked("protocol_loaded") is True
    # Same value again -> no change
    assert model.set_auto("protocol_loaded", True) is False
    # Detail change counts as a change
    assert model.set_auto("protocol_loaded", True, "now with detail") is True


def test_set_auto_ignores_unknown_and_manual_ids():
    model = PreRunChecklistModel()
    assert model.set_auto("does_not_exist", True) is False
    # Manual item cannot be flipped via set_auto
    assert model.set_auto("oil", True) is False
    assert model.is_checked("oil") is False


def test_set_manual_only_affects_manual_items():
    model = PreRunChecklistModel()
    assert model.set_manual("oil", True) is True
    assert model.is_checked("oil") is True
    # Auto item cannot be set via set_manual
    assert model.set_manual("protocol_loaded", True) is False


def test_reset_auto_clears_autos_but_keeps_manuals():
    model = PreRunChecklistModel()
    model.set_auto("protocol_loaded", True, "x")
    model.set_manual("oil", True)
    model.reset_auto()
    assert model.is_checked("protocol_loaded") is False
    assert model.get("protocol_loaded").detail == ""
    assert model.is_checked("oil") is True  # manual preserved


def test_is_ready_requires_all_required_checked():
    model = PreRunChecklistModel()
    assert model.is_ready() is False
    # Check every required item
    for item in model.required_items():
        if item.kind == AUTO:
            model.set_auto(item.item_id, True)
        else:
            model.set_manual(item.item_id, True)
    assert model.is_ready() is True
    # The two informational items may remain unchecked and readiness holds
    assert model.is_checked("focus_locked") is False


def test_summary_counts():
    model = PreRunChecklistModel()
    model.set_auto("protocol_loaded", True)
    model.set_manual("oil", True)
    s = model.summary()
    assert s["total"] == 14
    assert s["total_required"] == 9
    assert s["checked_required"] == 2
    assert s["ready"] == 0
    assert "NOT READY" in model.summary_text()


def test_informational_items_do_not_block_ready():
    model = PreRunChecklistModel()
    # Check every required item but leave informational ones unchecked
    for item in model.required_items():
        if item.kind == AUTO:
            model.set_auto(item.item_id, True)
        else:
            model.set_manual(item.item_id, True)
    assert model.is_ready() is True
    assert model.is_checked("fluidics_loaded") is False
    assert model.is_checked("objective_selected") is False


def test_default_items_are_independent_instances():
    a = default_items()
    b = default_items()
    a[0].checked = True
    assert b[0].checked is False


def test_unchecked_required_titles_tracks_required_only():
    model = PreRunChecklistModel()
    # Initially every required item is unchecked and reported by title.
    titles = model.unchecked_required_titles()
    assert len(titles) == len(model.required_items())
    assert all(isinstance(t, str) for t in titles)
    # Informational (non-required) items never appear here even when unchecked.
    for informational in ("fluidics_loaded", "objective_selected", "focus_locked"):
        assert model.get(informational).title not in titles
    # Checking a required item removes its title from the list.
    req = model.required_items()[0]
    if req.kind == AUTO:
        model.set_auto(req.item_id, True)
    else:
        model.set_manual(req.item_id, True)
    assert req.title not in model.unchecked_required_titles()


def test_unchecked_required_titles_empty_when_ready():
    model = PreRunChecklistModel()
    for item in model.required_items():
        if item.kind == AUTO:
            model.set_auto(item.item_id, True)
        else:
            model.set_manual(item.item_id, True)
    assert model.is_ready() is True
    assert model.unchecked_required_titles() == []


# ------------------------------------------------ Run-environment note helpers
def test_image_format_note_multipage_names_source_and_resume_caveat():
    text, sev = format_image_format_note("MULTI_PAGE_TIFF")
    assert "MULTI_PAGE_TIFF" in text
    assert "Preferences" in text  # authoritative source
    assert "ignored" in text  # the Acquisition dropdown decoy
    assert "INDIVIDUAL_IMAGES" in text  # resume caveat
    assert sev == SEV_INFO


def test_image_format_note_individual_has_no_resume_caveat():
    text, sev = format_image_format_note("INDIVIDUAL_IMAGES")
    assert "Resume-from-checkpoint" not in text
    assert sev == SEV_INFO


def test_image_format_note_accepts_enum_like_object():
    class _Enum:  # mimics FileSavingOption.OME_TIFF
        name = "OME_TIFF"

    text, sev = format_image_format_note(_Enum())
    assert "OME_TIFF" in text
    assert "Resume-from-checkpoint" in text  # not individual images


def test_image_format_note_handles_none():
    text, sev = format_image_format_note(None)
    assert sev == SEV_INFO
    assert text  # non-empty, does not raise


def test_fluidics_note_disabled_is_empty():
    assert format_fluidics_config_note("a.json", "b.json", False, None) == ("", SEV_INFO)


def test_fluidics_note_same_file_is_ok():
    text, sev = format_fluidics_config_note(
        "/x/MERFISH_config.json", "/x/MERFISH_config.json", True, True
    )
    assert sev == SEV_INFO
    assert "matches" in text.lower()


def test_fluidics_note_divergent_warns_with_both_paths():
    text, sev = format_fluidics_config_note(
        "/bundle/MERFISH_config.json", "./merfish_config/MERFISH_config.json", True, False
    )
    assert sev == SEV_WARN
    assert "/bundle/MERFISH_config.json" in text
    assert "Initialize" in text


def test_fluidics_note_unknown_sameness_warns():
    _, sev = format_fluidics_config_note("a.json", "b.json", True, None)
    assert sev == SEV_WARN


def test_fluidics_note_no_bundle_shows_live_only():
    text, sev = format_fluidics_config_note("", "live.json", True, None)
    assert "live.json" in text
    assert sev == SEV_INFO


def test_fov_freshness_recent_is_info():
    text, sev = format_fov_freshness_note("fov.csv", 1000.0, 1000.0 + 60)
    assert sev == SEV_INFO
    assert "fov.csv" in text


def test_fov_freshness_old_warns_recapture():
    now = 1_000_000.0
    text, sev = format_fov_freshness_note("fov.csv", now - 48 * 3600, now)
    assert sev == SEV_WARN
    assert "re-capture" in text.lower()


def test_fov_freshness_missing_mtime_is_info():
    text, sev = format_fov_freshness_note("fov.csv", None, 1000.0)
    assert sev == SEV_INFO
    assert "fov.csv" in text
