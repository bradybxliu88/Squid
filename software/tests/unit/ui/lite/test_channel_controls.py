from squid.ui.lite.channel_controls import build_persist_command
from squid.core.events import UpdateChannelConfigurationCommand


def test_build_persist_command_sets_fields():
    cmd = build_persist_command("10x", "Fluorescence 488 nm Ex", 33.0, 1.5, 60.0)
    assert isinstance(cmd, UpdateChannelConfigurationCommand)
    assert cmd.objective_name == "10x"
    assert cmd.config_name == "Fluorescence 488 nm Ex"
    assert cmd.exposure_time_ms == 33.0
    assert cmd.analog_gain == 1.5
    assert cmd.illumination_intensity == 60.0
