"""Service wrapper around the Squid laser engine driver.

Bridges the (Qt-free) driver's status/connection-loss callbacks to EventBus
state events, handles Wake-All / Sleep-All commands, and exposes a thread-safe
API for the LiveController warn-only check and the MultiPointWorker acquisition
gate.
"""

from __future__ import annotations

import threading
from typing import Callable, Iterable, List, Optional

from squid.backend.drivers.lighting.squid_laser_engine import (
    SquidLaserEngineBase,
    SquidLaserEngineStatus,
)
from squid.backend.services.base import BaseService
from squid.core.events import (
    EventBus,
    LaserEngineConnectionLost,
    LaserEngineStatusUpdated,
    SleepAllLasersCommand,
    WakeAllLasersCommand,
)


class LaserEngineService(BaseService):
    """Thread-safe service wrapping a SquidLaserEngine(_Simulation).

    The driver's callbacks fire on its background thread; this service forwards
    them to the EventBus (which marshals dispatch onto its own thread), so the
    UI never touches the driver thread directly.
    """

    def __init__(self, engine: SquidLaserEngineBase, event_bus: EventBus):
        super().__init__(event_bus)
        self._engine = engine
        self._lock = threading.RLock()

        engine.set_on_status(self._on_engine_status)
        engine.set_on_connection_lost(self._on_engine_connection_lost)

        self.subscribe(WakeAllLasersCommand, self._on_wake_all)
        self.subscribe(SleepAllLasersCommand, self._on_sleep_all)

    @property
    def READY_TIMEOUT_S(self) -> float:
        return self._engine.READY_TIMEOUT_S

    # ── Driver-thread callbacks → EventBus ──────────────────────────────────

    def _on_engine_status(self, status: SquidLaserEngineStatus) -> None:
        self.publish(LaserEngineStatusUpdated(status=status))

    def _on_engine_connection_lost(self, message: str) -> None:
        self.publish(LaserEngineConnectionLost(message=message))

    # ── Command handlers ────────────────────────────────────────────────────

    def _on_wake_all(self, _cmd: WakeAllLasersCommand) -> None:
        self.wake_up_all()

    def _on_sleep_all(self, _cmd: SleepAllLasersCommand) -> None:
        self.sleep_all()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            self._engine.start()

    def close(self) -> None:
        with self._lock:
            self._engine.close()

    def shutdown(self):
        try:
            self.close()
        finally:
            super().shutdown()

    # ── Pass-through API ────────────────────────────────────────────────────

    def wake_up(self, channel_key: str) -> None:
        with self._lock:
            self._engine.wake_up(channel_key)

    def wake_up_all(self) -> None:
        with self._lock:
            self._engine.wake_up_all()

    def sleep_all(self) -> None:
        with self._lock:
            self._engine.sleep_all()

    def get_latest_status(self) -> Optional[SquidLaserEngineStatus]:
        return self._engine.get_latest_status()

    def is_connection_lost(self) -> bool:
        return self._engine.is_connection_lost()

    def channel_keys_for_wavelengths(self, wavelengths: Iterable[int]) -> List[str]:
        return self._engine.channel_keys_for_wavelengths(wavelengths)

    def wait_until_ready(
        self,
        channel_keys: List[str],
        timeout_s: float,
        cancel_fn: Callable[[], bool] = lambda: False,
    ) -> bool:
        # No service lock: this blocks for the warm-up duration and must not
        # serialize status reads or wake/sleep commands. The driver is internally
        # thread-safe.
        return self._engine.wait_until_ready(channel_keys, timeout_s=timeout_s, cancel_fn=cancel_fn)
