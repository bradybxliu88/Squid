from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Any, Callable, Optional

import cv2
import imageio as iio
import numpy as np
from qtpy.QtCore import QObject, Signal

import squid.core.logging
import squid.core.utils.hardware_utils as utils
from squid.backend.io.utils_acquisition import get_image_filepath

log = squid.core.logging.get_logger(__name__)


@dataclass
class _DefaultRecordingChannel:
    """Stand-in for an AcquisitionChannel when no live channel is set during recording.

    Only `.name` is consulted for the filename; the other fields keep frames.csv
    rows uniform when no channel is available.
    """

    name: str = "live"
    exposure_time: float = 0.0
    analog_gain: float = 0.0
    illumination_intensity: float = 0.0


class ImageSaver(QObject):
    stop_recording = Signal()

    def __init__(self) -> None:
        QObject.__init__(self)
        self.base_path: str = "./"
        self.experiment_ID: str = ""
        self.max_num_image_per_folder: int = 1000
        self.queue: Queue = Queue(10)  # max 10 items in the queue
        self.image_lock: Lock = Lock()
        self.stop_signal_received: bool = False
        self.counter: int = 0
        self.recording_start_time: float = 0
        self.recording_time_limit: float = -1
        self._channel_provider: Optional[Callable[[], Optional[Any]]] = None
        self._csv_file = None
        self._csv_writer = None
        self._dropped_count: int = 0
        self._last_queue_full_warning_ts: float = 0.0
        self._stop_requested_from_writer: bool = False
        # Per-recording diagnostics (reset in start_new_experiment, summarized in stop_experiment).
        self._imwrite_ns_total: int = 0
        self._imwrite_ns_max: int = 0
        self._imwrite_count: int = 0
        self._silent_write_failures: int = 0
        self._queue_wait_ns_total: int = 0
        self._queue_wait_count: int = 0
        self._enqueue_call_count: int = 0
        # Start the saver thread last, after all state it touches has been initialized.
        self.thread: Thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()

    def set_channel_provider(self, provider: Optional[Callable[[], Optional[Any]]]) -> None:
        """Register a callable returning the active live channel (or None).

        Called per-frame from the saver thread. Cross-thread reads of the
        channel reference are GIL-atomic, so no lock is needed; the GUI thread
        just swaps the reference when the user toggles channels.
        """
        self._channel_provider = provider

    def process_queue(self) -> None:
        while True:
            if self.stop_signal_received:
                return
            get_started_ns = time.perf_counter_ns()
            try:
                image, frame_id, timestamp = self.queue.get(timeout=0.1)
            except Empty:
                continue
            self._queue_wait_ns_total += time.perf_counter_ns() - get_started_ns
            self._queue_wait_count += 1
            try:
                # A Qt-queued enqueue slot can arrive after stop_experiment cleared state;
                # discard those frames instead of writing to <base>/0/.
                if not self.experiment_ID:
                    continue
                with self.image_lock:
                    folder_id = self.counter // self.max_num_image_per_folder
                    file_id = self.counter % self.max_num_image_per_folder
                    save_dir = os.path.join(self.base_path, self.experiment_ID, str(folder_id))
                    if file_id == 0:
                        utils.ensure_directory_exists(save_dir)

                    channel = (
                        self._channel_provider() if self._channel_provider else None
                    ) or _DefaultRecordingChannel()
                    channel_name = getattr(channel, "name", "live")
                    # cv2.imwrite handles both uint8 (BMP/PNG/TIFF) and uint16 (TIFF/PNG).
                    # ~10x faster than imageio for uint8, ~2-3x faster for uint16.
                    saving_path = get_image_filepath(save_dir, str(file_id), channel_name, image.dtype)
                    imwrite_started_ns = time.perf_counter_ns()
                    ok = cv2.imwrite(saving_path, image)
                    imwrite_ns = time.perf_counter_ns() - imwrite_started_ns
                    self._imwrite_ns_total += imwrite_ns
                    if imwrite_ns > self._imwrite_ns_max:
                        self._imwrite_ns_max = imwrite_ns

                    if not ok:
                        # cv2.imwrite returns False (without raising) on bad path, missing
                        # codec, unsupported dtype, etc. Don't bump counter or write a CSV
                        # row that would point at a file that isn't on disk.
                        self._silent_write_failures += 1
                        log.warning(f"cv2.imwrite returned False for {saving_path}; frame {frame_id} skipped")
                        continue

                    self._imwrite_count += 1
                    # Increment counter only after a verified-successful image write — if the
                    # CSV row write below raises, the next frame must not reuse this file_id
                    # and overwrite what we just saved.
                    self.counter += 1

                    if self._csv_writer is not None:
                        rel_path = os.path.relpath(saving_path, os.path.join(self.base_path, self.experiment_ID))
                        self._csv_writer.writerow(
                            [
                                frame_id,
                                datetime.fromtimestamp(timestamp).isoformat(),
                                channel_name,
                                getattr(channel, "exposure_time", 0.0),
                                getattr(channel, "analog_gain", 0.0),
                                getattr(channel, "illumination_intensity", 0.0),
                                rel_path,
                            ]
                        )
                        # No per-row flush: it's an fsync per frame on most filesystems
                        # and was the second-largest contributor to the FPS regression.
                        # stop_experiment() flushes via close().
            except OSError as e:
                if not self._stop_requested_from_writer:
                    self._stop_requested_from_writer = True
                    log.error(f"Writer fatal error: {e}; stopping recording")
                    self.stop_recording.emit()
                else:
                    log.warning(f"Writer error during shutdown: {e}")
            except Exception as e:
                log.warning(f"Failed to write frame {frame_id}: {e}")
            finally:
                self.queue.task_done()

    def enqueue(self, image: np.ndarray, frame_id: int, timestamp: float) -> None:
        self._enqueue_call_count += 1
        try:
            self.queue.put_nowait([image, frame_id, timestamp])
        except Full:
            self._dropped_count += 1
            now = time.time()
            if now - self._last_queue_full_warning_ts >= 1.0:
                log.warning(f"Image queue full; frame {frame_id} dropped")
                self._last_queue_full_warning_ts = now
            return

        if self.recording_time_limit > 0 and time.time() - self.recording_start_time >= self.recording_time_limit:
            log.info(f"Auto-stopping: time limit reached ({self.recording_time_limit}s)")
            self.stop_recording.emit()

    def set_base_path(self, path: str) -> None:
        self.base_path = path

    def set_recording_time_limit(self, time_limit: float) -> None:
        self.recording_time_limit = time_limit

    def start_new_experiment(self, experiment_ID: str, add_timestamp: bool = True) -> None:
        # Defensively close any prior recording in case the caller didn't.
        self.stop_experiment()

        if add_timestamp:
            self.experiment_ID = experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        else:
            self.experiment_ID = experiment_ID
        self.recording_start_time = time.time()
        self.counter = 0
        self._dropped_count = 0
        self._last_queue_full_warning_ts = 0.0
        self._stop_requested_from_writer = False
        self._imwrite_ns_total = 0
        self._imwrite_ns_max = 0
        self._imwrite_count = 0
        self._silent_write_failures = 0
        self._queue_wait_ns_total = 0
        self._queue_wait_count = 0
        self._enqueue_call_count = 0

        experiment_dir = os.path.join(self.base_path, self.experiment_ID)
        try:
            utils.ensure_directory_exists(experiment_dir)
        except Exception as e:
            log.error(f"Failed to create experiment directory {experiment_dir}: {e}")
            self.experiment_ID = ""
            raise

        csv_path = os.path.join(experiment_dir, "frames.csv")
        try:
            self._csv_file = open(csv_path, "w", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow(
                ["frame_id", "timestamp_iso", "channel", "exposure_ms", "gain", "illumination_intensity", "file"]
            )
            self._csv_file.flush()
        except OSError as e:
            log.error(f"Failed to open {csv_path}: {e}")
            # If open() succeeded but writerow/flush raised, the handle is open — close it
            # before nulling the ref so we don't leak the fd.
            if self._csv_file is not None:
                try:
                    self._csv_file.close()
                except OSError:
                    pass
            self._csv_file = None
            self._csv_writer = None
            self.experiment_ID = ""
            raise

        log.info(f"Recording started: id={self.experiment_ID}, dir={experiment_dir}")
        if self._channel_provider is None:
            log.warning("channel_provider not set; frames tagged with default 'live' channel")
        elif self._channel_provider() is None:
            log.warning(
                "channel_provider returned None at recording start; "
                "frames will be tagged 'live' until a live channel is selected"
            )

    def stop_experiment(self) -> None:
        """Finalize the current recording: drain the queue, close frames.csv, log summary.

        Called by the widget on Stop and on time-limit auto-stop. Idempotent.
        Drains the queue first so any buffered frames land in the current
        experiment dir before experiment_ID is cleared — without that, the
        saver thread races and writes to a non-existent directory.
        """
        # Block until the saver thread has processed every buffered frame.
        # Safe because the caller has already stopped the streamHandler, so no
        # new items can be enqueued; queue.task_done() runs in finally so a
        # write exception cannot leave us hanging here.
        self.queue.join()
        if self._csv_file is not None:
            try:
                self._csv_file.close()
            finally:
                self._csv_file = None
                self._csv_writer = None
        if self.experiment_ID:
            duration = time.time() - self.recording_start_time
            log.info(
                f"Recording stopped: frames_saved={self.counter}, "
                f"dropped={self._dropped_count}, duration={duration:.1f}s"
            )
            if self._imwrite_count > 0:
                avg_imwrite_ms = self._imwrite_ns_total / self._imwrite_count / 1e6
                max_imwrite_ms = self._imwrite_ns_max / 1e6
                avg_queue_wait_ms = (
                    self._queue_wait_ns_total / self._queue_wait_count / 1e6 if self._queue_wait_count else 0.0
                )
                input_fps = self._enqueue_call_count / duration if duration > 0 else 0.0
                save_fps = self._imwrite_count / duration if duration > 0 else 0.0
                log.info(
                    f"Saver diagnostics: "
                    f"imwrite_avg={avg_imwrite_ms:.1f}ms, "
                    f"imwrite_max={max_imwrite_ms:.1f}ms, "
                    f"queue_wait_avg={avg_queue_wait_ms:.1f}ms, "
                    f"input={input_fps:.1f}fps ({self._enqueue_call_count} enqueues, {self._dropped_count} drops), "
                    f"saved={save_fps:.1f}fps, "
                    f"silent_write_failures={self._silent_write_failures}"
                )
            self.experiment_ID = ""

    def close(self) -> None:
        self.stop_experiment()
        self.stop_signal_received = True
        self.thread.join()


class ImageSaver_Tracking(QObject):
    def __init__(self, base_path: str, image_format: str = "bmp") -> None:
        QObject.__init__(self)
        self.base_path: str = base_path
        self.image_format: str = image_format
        self.max_num_image_per_folder: int = 1000
        self.queue: Queue = Queue(100)  # max 100 items in the queue
        self.image_lock: Lock = Lock()
        self.stop_signal_received: bool = False
        self.thread: Thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()

    def process_queue(self) -> None:
        while True:
            if self.stop_signal_received:
                return
            try:
                [image, frame_counter, postfix] = self.queue.get(timeout=0.1)
                self.image_lock.acquire(True)
                folder_ID = int(frame_counter / self.max_num_image_per_folder)
                file_ID = int(frame_counter % self.max_num_image_per_folder)
                if file_ID == 0:
                    utils.ensure_directory_exists(os.path.join(self.base_path, str(folder_ID)))
                if image.dtype == np.uint16:
                    saving_path = os.path.join(
                        self.base_path,
                        str(folder_ID),
                        str(file_ID) + "_" + str(frame_counter) + "_" + postfix + ".tiff",
                    )
                    iio.imwrite(saving_path, image)
                else:
                    saving_path = os.path.join(
                        self.base_path,
                        str(folder_ID),
                        str(file_ID) + "_" + str(frame_counter) + "_" + postfix + "." + self.image_format,
                    )
                    cv2.imwrite(saving_path, image)
                self.queue.task_done()
                self.image_lock.release()
            except Exception:
                pass

    def enqueue(self, image: np.ndarray, frame_counter: int, postfix: str) -> None:
        try:
            self.queue.put_nowait([image, frame_counter, postfix])
        except Exception:
            print("imageSaver queue is full, image discarded")

    def close(self) -> None:
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()

