"""Tests for LaserEngineService — driver callbacks → EventBus + command handling."""

import time

import pytest

from squid.backend.drivers.lighting.squid_laser_engine import (
    LaserChannelState,
    SquidLaserEngine_Simulation,
)
from squid.backend.services.laser_engine_service import LaserEngineService
from squid.core.events import (
    EventBus,
    LaserEngineConnectionLost,
    LaserEngineStatusUpdated,
    SleepAllLasersCommand,
    WakeAllLasersCommand,
)


@pytest.fixture
def bus():
    b = EventBus()
    b.start()
    yield b
    b.stop()


@pytest.fixture
def service(bus):
    engine = SquidLaserEngine_Simulation(query_interval_s=0.05, transition_seconds=0.1)
    svc = LaserEngineService(engine, bus)
    svc.start()
    yield svc
    svc.close()


def test_status_callback_published_as_event(service, bus):
    received = []
    bus.subscribe(LaserEngineStatusUpdated, lambda e: received.append(e.status))
    # Drive a change so the dirty-gated sim tick publishes.
    service.wake_up("405")
    time.sleep(0.2)
    bus.drain()
    assert received, "expected at least one LaserEngineStatusUpdated"
    assert received[-1].channels["405"] is not None


def test_connection_lost_published_as_event(service, bus):
    received = []
    bus.subscribe(LaserEngineConnectionLost, lambda e: received.append(e.message))
    service._engine.force_connection_lost("test drop")
    time.sleep(0.1)
    bus.drain()
    assert received == ["test drop"]
    assert service.is_connection_lost() is True


def test_wake_all_command_wakes_engine(service, bus):
    service._engine.sleep_all()
    time.sleep(0.3)  # let them reach SLEEP
    bus.publish(WakeAllLasersCommand())
    bus.drain()
    time.sleep(0.5)  # warm-up cycle
    status = service.get_latest_status()
    assert status.is_ready_for(["405", "470", "55x", "638", "730"])


def test_sleep_all_command_sleeps_engine(service, bus):
    time.sleep(0.4)  # all active
    bus.publish(SleepAllLasersCommand())
    bus.drain()
    time.sleep(0.3)
    status = service.get_latest_status()
    assert not status.channels["405"].is_ready


def test_channel_keys_for_wavelengths_passthrough(service):
    assert service.channel_keys_for_wavelengths([488, 561, 640]) == ["470", "55x", "638"]


def test_wait_until_ready_passthrough(service):
    time.sleep(0.4)
    assert service.wait_until_ready(["405"], timeout_s=1.0) is True
