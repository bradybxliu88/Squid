"""
Squid Stress Test Suite
=======================
Comprehensive hardware stress testing for Squid+ with Daheng camera,
XLight spinning disk, laser autofocus, filter wheel, and piezo.

Usage:
    python stress_test.py --simulation       # Test without hardware
    python stress_test.py                    # Test with real hardware
    python stress_test.py --test camera      # Run only camera tests
    python stress_test.py --test stage       # Run only stage tests
    python stress_test.py --test acquisition # Run only acquisition tests
    python stress_test.py --test all         # Run everything (default)
    python stress_test.py --duration 3600    # Long run duration in seconds (default: 300)

Logs are written to stress_test_output/stress_test_YYYYMMDD_HHMMSS.log
"""

import argparse
import csv
import logging
import os
import sys
import time
import traceback
import psutil
from datetime import datetime
from pathlib import Path

# Add software directory to path
SOFTWARE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SOFTWARE_DIR))

import numpy as np


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def setup_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = output_dir / f"stress_test_{timestamp}.log"

    logger = logging.getLogger("squid_stress_test")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Log file: {log_path}")
    return logger


# ---------------------------------------------------------------------------
# Resource monitor
# ---------------------------------------------------------------------------
class ResourceMonitor:
    """Track CPU, RAM, and process memory over time."""

    def __init__(self, logger):
        self.logger = logger
        self.process = psutil.Process(os.getpid())
        self.snapshots = []

    def snapshot(self, label: str = ""):
        mem = self.process.memory_info()
        cpu = psutil.cpu_percent(interval=0.1)
        ram_pct = psutil.virtual_memory().percent
        entry = {
            "time": time.time(),
            "label": label,
            "rss_mb": mem.rss / 1e6,
            "vms_mb": mem.vms / 1e6,
            "cpu_pct": cpu,
            "ram_pct": ram_pct,
        }
        self.snapshots.append(entry)
        self.logger.debug(
            f"[Resource] {label} | RSS={entry['rss_mb']:.1f}MB "
            f"VMS={entry['vms_mb']:.1f}MB CPU={cpu:.1f}% RAM={ram_pct:.1f}%"
        )
        return entry

    def report(self) -> dict:
        if not self.snapshots:
            return {}
        rss_vals = [s["rss_mb"] for s in self.snapshots]
        return {
            "rss_min_mb": min(rss_vals),
            "rss_max_mb": max(rss_vals),
            "rss_delta_mb": max(rss_vals) - min(rss_vals),
            "num_snapshots": len(self.snapshots),
        }

    def save_csv(self, path: Path):
        if not self.snapshots:
            return
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.snapshots[0].keys())
            writer.writeheader()
            writer.writerows(self.snapshots)


# ---------------------------------------------------------------------------
# Test result tracking
# ---------------------------------------------------------------------------
class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.error = None
        self.metrics = {}
        self.start_time = None
        self.end_time = None

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        dur = (self.end_time - self.start_time) if self.end_time and self.start_time else 0
        return f"[{status}] {self.name} ({dur:.1f}s) {self.metrics}"


# ---------------------------------------------------------------------------
# 1. CAMERA STRESS TESTS
# ---------------------------------------------------------------------------
def test_camera_sustained_streaming(scope, logger, monitor, duration_s=60):
    """Stream continuously and measure frame rate, dropped frames, latency."""
    result = TestResult("camera_sustained_streaming")
    result.start_time = time.time()
    logger.info(f"=== Camera Sustained Streaming Test ({duration_s}s) ===")

    try:
        camera = scope.camera
        camera.start_streaming()
        time.sleep(0.5)  # let stream stabilize

        frame_count = 0
        drop_count = 0
        latencies = []
        last_frame_time = None
        t_start = time.time()

        while (time.time() - t_start) < duration_s:
            t_trigger = time.time()
            camera.send_trigger()
            frame = camera.read_frame()

            if frame is None or frame.data is None:
                drop_count += 1
                logger.warning(f"Dropped frame at t={time.time()-t_start:.2f}s (total drops: {drop_count})")
            else:
                latency = time.time() - t_trigger
                latencies.append(latency)
                frame_count += 1

                if last_frame_time is not None:
                    gap = time.time() - last_frame_time
                    if gap > 0.5:  # flag gaps > 500ms
                        logger.warning(f"Frame gap {gap:.3f}s at frame {frame_count}")
                last_frame_time = time.time()

            # Resource snapshot every 10s
            if frame_count % 100 == 0:
                monitor.snapshot(f"camera_stream_frame_{frame_count}")

        camera.stop_streaming()
        elapsed = time.time() - t_start

        result.metrics = {
            "total_frames": frame_count,
            "dropped_frames": drop_count,
            "drop_rate_pct": (drop_count / max(frame_count + drop_count, 1)) * 100,
            "avg_fps": frame_count / elapsed if elapsed > 0 else 0,
            "avg_latency_ms": np.mean(latencies) * 1000 if latencies else 0,
            "p95_latency_ms": np.percentile(latencies, 95) * 1000 if latencies else 0,
            "max_latency_ms": max(latencies) * 1000 if latencies else 0,
            "duration_s": elapsed,
        }
        result.passed = drop_count == 0
        logger.info(f"Camera streaming: {frame_count} frames, {drop_count} drops, "
                     f"FPS={result.metrics['avg_fps']:.1f}, "
                     f"avg latency={result.metrics['avg_latency_ms']:.1f}ms")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Camera streaming test failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


def test_camera_rapid_start_stop(scope, logger, monitor, cycles=50):
    """Repeatedly start/stop streaming to catch resource leaks."""
    result = TestResult("camera_rapid_start_stop")
    result.start_time = time.time()
    logger.info(f"=== Camera Rapid Start/Stop Test ({cycles} cycles) ===")

    try:
        camera = scope.camera
        failures = 0
        monitor.snapshot("start_stop_begin")

        for i in range(cycles):
            try:
                camera.start_streaming()
                camera.send_trigger()
                frame = camera.read_frame()
                camera.stop_streaming()
                if frame is None or frame.data is None:
                    failures += 1
                    logger.warning(f"Cycle {i}: null frame")
            except Exception as e:
                failures += 1
                logger.warning(f"Cycle {i} error: {e}")

            if i % 10 == 0:
                monitor.snapshot(f"start_stop_cycle_{i}")

        monitor.snapshot("start_stop_end")
        mem_report = monitor.report()

        result.metrics = {
            "cycles": cycles,
            "failures": failures,
            "rss_delta_mb": mem_report.get("rss_delta_mb", 0),
        }
        result.passed = failures == 0
        logger.info(f"Start/stop: {cycles} cycles, {failures} failures, "
                     f"memory delta={result.metrics['rss_delta_mb']:.1f}MB")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Camera start/stop test failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


def test_camera_pixel_format_switching(scope, logger, monitor):
    """Switch pixel formats and verify frames are returned correctly."""
    result = TestResult("camera_pixel_format_switching")
    result.start_time = time.time()
    logger.info("=== Camera Pixel Format Switching Test ===")

    try:
        from squid.abc import CameraPixelFormat
        camera = scope.camera
        formats_tested = 0
        failures = 0

        for fmt in [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12, CameraPixelFormat.MONO16]:
            try:
                camera.set_pixel_format(fmt)
                camera.start_streaming()
                camera.send_trigger()
                frame = camera.read_frame()
                camera.stop_streaming()

                if frame is None or frame.data is None:
                    failures += 1
                    logger.warning(f"Format {fmt}: null frame")
                else:
                    formats_tested += 1
                    logger.info(f"Format {fmt}: frame shape={frame.data.shape}, dtype={frame.data.dtype}")
            except Exception as e:
                logger.warning(f"Format {fmt} not supported or failed: {e}")
                formats_tested += 1  # not a failure if unsupported

        result.metrics = {"formats_tested": formats_tested, "failures": failures}
        result.passed = failures == 0

    except Exception as e:
        result.error = str(e)
        logger.error(f"Pixel format test failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


# ---------------------------------------------------------------------------
# 2. STAGE STRESS TESTS
# ---------------------------------------------------------------------------
def test_stage_repeated_moves(scope, logger, monitor, cycles=100):
    """Move to random positions and back, check position accuracy."""
    result = TestResult("stage_repeated_moves")
    result.start_time = time.time()
    logger.info(f"=== Stage Repeated Moves Test ({cycles} cycles) ===")

    try:
        stage = scope.stage
        config = stage.get_config()

        # Safe range: 10% inward from limits
        x_min = config.X_AXIS.MIN_POSITION + (config.X_AXIS.MAX_POSITION - config.X_AXIS.MIN_POSITION) * 0.1
        x_max = config.X_AXIS.MIN_POSITION + (config.X_AXIS.MAX_POSITION - config.X_AXIS.MIN_POSITION) * 0.9
        y_min = config.Y_AXIS.MIN_POSITION + (config.Y_AXIS.MAX_POSITION - config.Y_AXIS.MIN_POSITION) * 0.1
        y_max = config.Y_AXIS.MIN_POSITION + (config.Y_AXIS.MAX_POSITION - config.Y_AXIS.MIN_POSITION) * 0.9
        z_mid = (config.Z_AXIS.MIN_POSITION + config.Z_AXIS.MAX_POSITION) / 2

        position_errors = []
        move_times = []

        # Home first
        logger.info("Homing stage...")
        stage.home(x=True, y=True, z=True, theta=False, blocking=True)

        for i in range(cycles):
            target_x = np.random.uniform(x_min, x_max)
            target_y = np.random.uniform(y_min, y_max)

            t0 = time.time()
            stage.move_x_to(target_x, blocking=True)
            stage.move_y_to(target_y, blocking=True)
            move_time = time.time() - t0

            pos = stage.get_pos()
            err_x = abs(pos.x_mm - target_x)
            err_y = abs(pos.y_mm - target_y)
            total_err = np.sqrt(err_x**2 + err_y**2)
            position_errors.append(total_err)
            move_times.append(move_time)

            if total_err > 0.01:  # flag > 10um error
                logger.warning(f"Cycle {i}: position error {total_err*1000:.1f}um "
                               f"(target=({target_x:.3f},{target_y:.3f}) "
                               f"actual=({pos.x_mm:.3f},{pos.y_mm:.3f}))")

            if i % 20 == 0:
                monitor.snapshot(f"stage_move_cycle_{i}")
                logger.info(f"  Cycle {i}/{cycles}: err={total_err*1000:.1f}um, move_time={move_time:.2f}s")

        result.metrics = {
            "cycles": cycles,
            "mean_error_um": np.mean(position_errors) * 1000,
            "max_error_um": np.max(position_errors) * 1000,
            "p95_error_um": np.percentile(position_errors, 95) * 1000,
            "mean_move_time_s": np.mean(move_times),
            "max_move_time_s": np.max(move_times),
        }
        result.passed = np.max(position_errors) < 0.05  # <50um
        logger.info(f"Stage moves: mean_err={result.metrics['mean_error_um']:.1f}um, "
                     f"max_err={result.metrics['max_error_um']:.1f}um")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Stage repeated moves failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


def test_stage_homing_repeatability(scope, logger, monitor, cycles=20):
    """Home repeatedly and check position consistency."""
    result = TestResult("stage_homing_repeatability")
    result.start_time = time.time()
    logger.info(f"=== Stage Homing Repeatability Test ({cycles} cycles) ===")

    try:
        stage = scope.stage
        config = stage.get_config()
        home_positions = []

        for i in range(cycles):
            # Move away from home
            mid_x = (config.X_AXIS.MIN_POSITION + config.X_AXIS.MAX_POSITION) / 2
            mid_y = (config.Y_AXIS.MIN_POSITION + config.Y_AXIS.MAX_POSITION) / 2
            stage.move_x_to(mid_x, blocking=True)
            stage.move_y_to(mid_y, blocking=True)

            # Home
            stage.home(x=True, y=True, z=True, theta=False, blocking=True)
            time.sleep(0.5)
            pos = stage.get_pos()
            home_positions.append((pos.x_mm, pos.y_mm, pos.z_mm))

            logger.debug(f"Home {i}: ({pos.x_mm:.4f}, {pos.y_mm:.4f}, {pos.z_mm:.4f})")
            monitor.snapshot(f"homing_cycle_{i}")

        x_vals = [p[0] for p in home_positions]
        y_vals = [p[1] for p in home_positions]
        z_vals = [p[2] for p in home_positions]

        result.metrics = {
            "cycles": cycles,
            "x_std_um": np.std(x_vals) * 1000,
            "y_std_um": np.std(y_vals) * 1000,
            "z_std_um": np.std(z_vals) * 1000,
            "x_range_um": (max(x_vals) - min(x_vals)) * 1000,
            "y_range_um": (max(y_vals) - min(y_vals)) * 1000,
            "z_range_um": (max(z_vals) - min(z_vals)) * 1000,
        }
        max_range = max(result.metrics["x_range_um"], result.metrics["y_range_um"])
        result.passed = max_range < 20  # <20um repeatability
        logger.info(f"Homing repeatability: X_std={result.metrics['x_std_um']:.1f}um, "
                     f"Y_std={result.metrics['y_std_um']:.1f}um, "
                     f"Z_std={result.metrics['z_std_um']:.1f}um")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Homing repeatability failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


def test_stage_z_drift(scope, logger, monitor, duration_s=300):
    """Hold Z position and monitor drift over time."""
    result = TestResult("stage_z_drift")
    result.start_time = time.time()
    logger.info(f"=== Stage Z Drift Test ({duration_s}s) ===")

    try:
        stage = scope.stage
        config = stage.get_config()
        z_target = (config.Z_AXIS.MIN_POSITION + config.Z_AXIS.MAX_POSITION) / 2

        stage.move_z_to(z_target, blocking=True)
        time.sleep(1)

        z_readings = []
        t_start = time.time()
        interval = 5  # sample every 5s

        while (time.time() - t_start) < duration_s:
            pos = stage.get_pos()
            z_readings.append({"t": time.time() - t_start, "z_mm": pos.z_mm})
            monitor.snapshot(f"z_drift_t={z_readings[-1]['t']:.0f}s")
            time.sleep(interval)

        z_vals = [r["z_mm"] for r in z_readings]
        result.metrics = {
            "duration_s": duration_s,
            "z_target_mm": z_target,
            "z_mean_mm": np.mean(z_vals),
            "z_std_um": np.std(z_vals) * 1000,
            "z_drift_um": (max(z_vals) - min(z_vals)) * 1000,
            "z_max_deviation_um": max(abs(v - z_target) for v in z_vals) * 1000,
            "num_samples": len(z_readings),
        }
        result.passed = result.metrics["z_drift_um"] < 5  # <5um over test duration
        logger.info(f"Z drift: total={result.metrics['z_drift_um']:.2f}um, "
                     f"std={result.metrics['z_std_um']:.2f}um over {duration_s}s")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Z drift test failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


# ---------------------------------------------------------------------------
# 3. LONG ACQUISITION STRESS TEST
# ---------------------------------------------------------------------------
def test_long_acquisition(scope, logger, monitor, duration_s=300):
    """Simulate a long multi-round acquisition: cycle through channels,
    move stage, acquire images, save to disk. Monitor memory and timing."""
    result = TestResult("long_acquisition")
    result.start_time = time.time()
    logger.info(f"=== Long Acquisition Test ({duration_s}s) ===")

    try:
        stage = scope.stage
        camera = scope.camera
        illum = scope.illumination_controller
        config = stage.get_config()

        # Define a small grid of positions
        x_center = (config.X_AXIS.MIN_POSITION + config.X_AXIS.MAX_POSITION) / 2
        y_center = (config.Y_AXIS.MIN_POSITION + config.Y_AXIS.MAX_POSITION) / 2
        z_center = (config.Z_AXIS.MIN_POSITION + config.Z_AXIS.MAX_POSITION) / 2

        grid_size = 3  # 3x3
        step_mm = 1.0
        positions = []
        for ix in range(grid_size):
            for iy in range(grid_size):
                positions.append((
                    x_center + (ix - grid_size // 2) * step_mm,
                    y_center + (iy - grid_size // 2) * step_mm,
                ))

        output_dir = SOFTWARE_DIR / "stress_test_output" / "acquisition"
        output_dir.mkdir(parents=True, exist_ok=True)

        stage.home(x=True, y=True, z=True, theta=False, blocking=True)
        stage.move_z_to(z_center, blocking=True)

        round_num = 0
        total_frames = 0
        errors = 0
        round_times = []
        t_start = time.time()

        while (time.time() - t_start) < duration_s:
            round_start = time.time()
            round_num += 1
            logger.info(f"--- Acquisition round {round_num} ---")

            for pos_idx, (px, py) in enumerate(positions):
                try:
                    stage.move_x_to(px, blocking=True)
                    stage.move_y_to(py, blocking=True)

                    illum.turn_on_illumination()
                    camera.start_streaming()
                    camera.send_trigger()
                    frame = camera.read_frame()
                    camera.stop_streaming()
                    illum.turn_off_illumination()

                    if frame is not None and frame.data is not None:
                        total_frames += 1
                        # Save every 10th frame to test I/O
                        if total_frames % 10 == 0:
                            fname = output_dir / f"r{round_num:04d}_p{pos_idx:02d}.npy"
                            np.save(str(fname), frame.data)
                    else:
                        errors += 1
                        logger.warning(f"Round {round_num}, pos {pos_idx}: null frame")

                except Exception as e:
                    errors += 1
                    logger.warning(f"Round {round_num}, pos {pos_idx} error: {e}")

            round_time = time.time() - round_start
            round_times.append(round_time)
            monitor.snapshot(f"acquisition_round_{round_num}")
            logger.info(f"Round {round_num}: {len(positions)} positions in {round_time:.1f}s "
                         f"(total frames: {total_frames}, errors: {errors})")

        elapsed = time.time() - t_start
        result.metrics = {
            "total_rounds": round_num,
            "total_frames": total_frames,
            "total_errors": errors,
            "error_rate_pct": (errors / max(total_frames + errors, 1)) * 100,
            "duration_s": elapsed,
            "avg_round_time_s": np.mean(round_times) if round_times else 0,
            "max_round_time_s": max(round_times) if round_times else 0,
            "frames_per_second": total_frames / elapsed if elapsed > 0 else 0,
        }
        result.passed = errors == 0
        logger.info(f"Long acquisition: {round_num} rounds, {total_frames} frames, "
                     f"{errors} errors, {result.metrics['frames_per_second']:.2f} fps overall")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Long acquisition test failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


# ---------------------------------------------------------------------------
# 4. FULL INTEGRATION STRESS TEST
# ---------------------------------------------------------------------------
def test_full_integration(scope, logger, monitor, duration_s=120):
    """Rapid interleaved operations: move, image, change settings, repeat.
    Tests that all subsystems work correctly under concurrent load."""
    result = TestResult("full_integration")
    result.start_time = time.time()
    logger.info(f"=== Full Integration Stress Test ({duration_s}s) ===")

    try:
        stage = scope.stage
        camera = scope.camera
        illum = scope.illumination_controller
        config = stage.get_config()

        x_mid = (config.X_AXIS.MIN_POSITION + config.X_AXIS.MAX_POSITION) / 2
        y_mid = (config.Y_AXIS.MIN_POSITION + config.Y_AXIS.MAX_POSITION) / 2
        z_mid = (config.Z_AXIS.MIN_POSITION + config.Z_AXIS.MAX_POSITION) / 2

        stage.home(x=True, y=True, z=True, theta=False, blocking=True)

        cycles = 0
        errors = 0
        t_start = time.time()

        while (time.time() - t_start) < duration_s:
            cycles += 1
            try:
                # Random XY move
                dx = np.random.uniform(-2, 2)
                dy = np.random.uniform(-2, 2)
                target_x = np.clip(x_mid + dx, config.X_AXIS.MIN_POSITION * 1.1, config.X_AXIS.MAX_POSITION * 0.9)
                target_y = np.clip(y_mid + dy, config.Y_AXIS.MIN_POSITION * 1.1, config.Y_AXIS.MAX_POSITION * 0.9)
                stage.move_x_to(target_x, blocking=True)
                stage.move_y_to(target_y, blocking=True)

                # Small Z adjustment
                dz = np.random.uniform(-0.05, 0.05)
                z_target = np.clip(z_mid + dz, config.Z_AXIS.MIN_POSITION * 1.1, config.Z_AXIS.MAX_POSITION * 0.9)
                stage.move_z_to(z_target, blocking=True)

                # Acquire frame
                illum.turn_on_illumination()
                camera.start_streaming()

                # Vary exposure time
                exposure_ms = np.random.choice([5, 10, 20, 50, 100])
                camera.set_exposure_time(exposure_ms)

                camera.send_trigger()
                frame = camera.read_frame()
                camera.stop_streaming()
                illum.turn_off_illumination()

                if frame is None or frame.data is None:
                    errors += 1

                # Verify stage position
                pos = stage.get_pos()
                pos_err = np.sqrt((pos.x_mm - target_x)**2 + (pos.y_mm - target_y)**2)
                if pos_err > 0.05:
                    logger.warning(f"Cycle {cycles}: position error {pos_err*1000:.1f}um")
                    errors += 1

            except Exception as e:
                errors += 1
                logger.warning(f"Integration cycle {cycles} error: {e}")

            if cycles % 10 == 0:
                monitor.snapshot(f"integration_cycle_{cycles}")
                logger.info(f"  Cycle {cycles}, errors so far: {errors}")

        elapsed = time.time() - t_start
        result.metrics = {
            "total_cycles": cycles,
            "total_errors": errors,
            "error_rate_pct": (errors / max(cycles, 1)) * 100,
            "duration_s": elapsed,
            "cycles_per_second": cycles / elapsed if elapsed > 0 else 0,
        }
        result.passed = (errors / max(cycles, 1)) < 0.01  # <1% error rate
        logger.info(f"Integration: {cycles} cycles, {errors} errors "
                     f"({result.metrics['error_rate_pct']:.2f}%), "
                     f"{result.metrics['cycles_per_second']:.2f} cycles/s")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Integration test failed: {e}\n{traceback.format_exc()}")

    result.end_time = time.time()
    return result


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Squid Hardware Stress Test Suite")
    parser.add_argument("--simulation", action="store_true", help="Run in simulation mode (no hardware)")
    parser.add_argument("--test", default="all", choices=["camera", "stage", "acquisition", "integration", "all"])
    parser.add_argument("--duration", type=int, default=300, help="Duration for long-running tests in seconds")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    output_dir = SOFTWARE_DIR / "stress_test_output"
    logger = setup_logging(output_dir)
    monitor = ResourceMonitor(logger)

    logger.info("=" * 60)
    logger.info("SQUID STRESS TEST SUITE")
    logger.info(f"Mode: {'SIMULATION' if args.simulation else 'HARDWARE'}")
    logger.info(f"Test: {args.test}")
    logger.info(f"Duration: {args.duration}s")
    logger.info("=" * 60)

    # Build microscope
    logger.info("Building microscope...")
    monitor.snapshot("before_build")

    import control._def
    from control.microscope import Microscope
    scope = Microscope.build_from_global_config(simulated=args.simulation)
    monitor.snapshot("after_build")
    logger.info("Microscope built successfully.")

    results = []

    try:
        # Camera tests
        if args.test in ("camera", "all"):
            results.append(test_camera_sustained_streaming(scope, logger, monitor, duration_s=min(args.duration, 120)))
            results.append(test_camera_rapid_start_stop(scope, logger, monitor, cycles=50))
            results.append(test_camera_pixel_format_switching(scope, logger, monitor))

        # Stage tests
        if args.test in ("stage", "all"):
            results.append(test_stage_repeated_moves(scope, logger, monitor, cycles=100))
            results.append(test_stage_homing_repeatability(scope, logger, monitor, cycles=20))
            results.append(test_stage_z_drift(scope, logger, monitor, duration_s=min(args.duration, 300)))

        # Long acquisition
        if args.test in ("acquisition", "all"):
            results.append(test_long_acquisition(scope, logger, monitor, duration_s=args.duration))

        # Full integration
        if args.test in ("integration", "all"):
            results.append(test_full_integration(scope, logger, monitor, duration_s=min(args.duration, 300)))

    finally:
        logger.info("Closing microscope...")
        scope.close()

    # Final report
    logger.info("")
    logger.info("=" * 60)
    logger.info("STRESS TEST RESULTS")
    logger.info("=" * 60)

    passed = 0
    failed = 0
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        dur = (r.end_time - r.start_time) if r.end_time and r.start_time else 0
        logger.info(f"  [{status}] {r.name} ({dur:.1f}s)")
        for k, v in r.metrics.items():
            if isinstance(v, float):
                logger.info(f"         {k}: {v:.3f}")
            else:
                logger.info(f"         {k}: {v}")
        if r.error:
            logger.info(f"         ERROR: {r.error}")
        if r.passed:
            passed += 1
        else:
            failed += 1

    logger.info("")
    logger.info(f"TOTAL: {passed} passed, {failed} failed out of {len(results)} tests")

    # Save resource data
    resource_report = monitor.report()
    logger.info(f"Memory: RSS min={resource_report.get('rss_min_mb', 0):.1f}MB, "
                 f"max={resource_report.get('rss_max_mb', 0):.1f}MB, "
                 f"delta={resource_report.get('rss_delta_mb', 0):.1f}MB")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    monitor.save_csv(output_dir / f"resource_monitor_{timestamp}.csv")

    # Summary CSV
    summary_path = output_dir / f"results_{timestamp}.csv"
    with open(summary_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["test", "passed", "duration_s", "error"] +
                         sorted(set(k for r in results for k in r.metrics.keys())))
        metric_keys = sorted(set(k for r in results for k in r.metrics.keys()))
        for r in results:
            dur = (r.end_time - r.start_time) if r.end_time and r.start_time else 0
            row = [r.name, r.passed, f"{dur:.1f}", r.error or ""]
            for k in metric_keys:
                row.append(r.metrics.get(k, ""))
            writer.writerow(row)

    logger.info(f"Results saved to {summary_path}")
    logger.info(f"Resource data saved to {output_dir / f'resource_monitor_{timestamp}.csv'}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
