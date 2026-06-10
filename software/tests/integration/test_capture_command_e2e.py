import os
import time

from squid.application import ApplicationContext
from squid.core.events import (
    event_bus,
    CaptureMultichannelCommand,
    CaptureCompleted,
    CaptureFailed,
)


def test_capture_command_writes_file_and_emits_completed(tmp_path):
    ctx = ApplicationContext(simulation=True)
    received = []
    event_bus.subscribe(CaptureCompleted, received.append)
    event_bus.subscribe(CaptureFailed, received.append)
    try:
        objective = ctx.microscope.objective_store.current_objective
        ccm = ctx.microscope.channel_configuration_manager
        names = [c.name for c in ccm.get_configurations(objective, enabled_only=True)][:2]

        event_bus.publish(CaptureMultichannelCommand(
            channel_names=names, objective=objective,
            save_directory=str(tmp_path), file_prefix="e2e",
        ))

        # EventBus dispatches on a background thread — wait for completion.
        for _ in range(100):
            if received:
                break
            time.sleep(0.05)

        assert received, "no CaptureCompleted/CaptureFailed received"
        assert isinstance(received[-1], CaptureCompleted), getattr(received[-1], "reason", "")
        assert os.path.exists(received[-1].save_path)
    finally:
        ctx.shutdown()
